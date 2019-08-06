import cv2
import numpy as np
import os
import tensorflow as tf
from psenet import config
from psenet.data.utils import preprocess


class Dataset:
    def __init__(
        self,
        dataset_dir,
        batch_size,
        resize_length=config.RESIZE_LENGTH,
        min_scale=config.MIN_SCALE,
        kernel_num=config.KERNEL_NUM,
        num_readers=config.NUM_READERS,
        should_shuffle=False,
        should_repeat=False,
        should_augment=True,
    ):
        self.dataset_dir = dataset_dir
        self.batch_size = batch_size
        self.resize_length = resize_length
        self.num_readers = num_readers
        self.should_shuffle = should_shuffle
        self.should_repeat = should_repeat
        self.min_scale = min_scale
        self.kernel_num = kernel_num
        self.should_augment = should_augment

    def _parse_example(self, example_prototype):
        features = {
            "image/encoded": tf.io.FixedLenFeature(
                (), tf.string, default_value=""
            ),
            "image/filename": tf.io.FixedLenFeature(
                (), tf.string, default_value=""
            ),
            "image/format": tf.io.FixedLenFeature(
                (), tf.string, default_value="jpeg"
            ),
            "image/height": tf.io.FixedLenFeature(
                (), tf.int64, default_value=0
            ),
            "image/width": tf.io.FixedLenFeature(
                (), tf.int64, default_value=0
            ),
            "image/text/tags/encoded": tf.io.FixedLenFeature(
                (), tf.string, default_value=""
            ),
            "image/text/boxes/count": tf.io.FixedLenFeature(
                (), tf.int64, default_value=0
            ),
            "image/text/boxes/encoded": tf.io.VarLenFeature(tf.float32),
        }
        parsed_features = tf.io.parse_single_example(
            example_prototype, features
        )
        image_data = parsed_features["image/encoded"]
        image = tf.cond(
            tf.image.is_jpeg(image_data),
            lambda: tf.image.decode_jpeg(image_data, 3),
            lambda: tf.image.decode_png(image_data, 3),
        )
        bboxes = tf.sparse.to_dense(
            parsed_features["image/text/boxes/encoded"], default_value=0
        )
        n_bboxes = tf.cast(parsed_features["image/text/boxes/count"], "int64")
        bboxes_shape = tf.stack([n_bboxes, config.BBOX_SIZE])
        bboxes = tf.reshape(bboxes, bboxes_shape)
        image_name = parsed_features["image/filename"]
        if image_name is None:
            image_name = tf.constant("")
        tags = parsed_features["image/text/tags/encoded"]
        sample = {
            config.BBOXES: bboxes,
            config.HEIGHT: parsed_features["image/height"],
            config.IMAGE_NAME: image_name,
            config.IMAGE: image,
            config.WIDTH: parsed_features["image/width"],
            config.TAGS: tags,
        }
        return sample

    def _process_tagged_bboxes(self, bboxes, tags, height, width):
        tags = str(tags)
        gt_text = np.zeros([height, width], dtype="uint8")
        mask = np.ones([height, width], dtype="uint8")
        bboxes_count, bboxes_width = np.asarray(bboxes.shape).astype("int64")[
            :2
        ]
        if bboxes_count > 0:
            bboxes = np.reshape(
                bboxes * ([width, height] * 4),
                (bboxes_count, int(bboxes_width / 2), 2),
            ).astype("int32")
            for i in range(bboxes_count):
                cv2.drawContours(gt_text, [bboxes[i]], -1, i + 1, -1)
                if tags[i] != "1":
                    cv2.drawContours(mask, [bboxes[i]], -1, 0, -1)

        gt_kernels = []
        for i in range(1, self.kernel_num):
            rate = 1.0 - (1.0 - self.min_scale) / (self.kernel_num - 1) * i
            gt_kernel = np.zeros([height, width], dtype="uint8")
            kernel_bboxes = preprocess.shrink(bboxes, rate)
            for i in range(bboxes_count):
                cv2.drawContours(gt_kernel, [kernel_bboxes[i]], -1, 1, -1)
            gt_kernels.append(gt_kernel)
        return gt_kernels, gt_text, mask

    def _preprocess_example(self, sample):
        image = sample[config.IMAGE]
        tags = sample[config.TAGS]
        bboxes = sample[config.BBOXES]

        if self.should_augment:
            image = preprocess.random_scale(image, self.resize_length)
        else:
            image = preprocess.scale(image, self.resize_length)
        image_shape = tf.shape(image)
        height = image_shape[0]
        width = image_shape[1]
        processed = tf.py_function(
            func=self._process_tagged_bboxes,
            inp=[bboxes, tags, height, width],
            Tout=[tf.uint8, tf.uint8, tf.uint8],
        )
        gt_kernels = processed[0]
        gt_text = processed[1]
        mask = processed[2]

        if self.should_augment:
            tensors = [image, gt_text, mask]
            for idx in range(1, self.kernel_num):
                tensors.append(gt_kernels[idx - 1])
            tensors = preprocess.random_flip(tensors)
            tensors = preprocess.random_rotate(tensors)
            tensors = preprocess.background_random_crop(tensors, gt_text)
            image, gt_text, mask, gt_kernels = (
                tensors[0],
                tensors[1],
                tensors[2],
                tensors[3:],
            )
        gt_text = tf.cast(gt_text, tf.float32)
        gt_text = tf.sign(gt_text)
        gt_text = tf.cast(gt_text, tf.uint8)
        gt_text = tf.expand_dims(gt_text, axis=0)
        mask = tf.expand_dims(mask, axis=0)
        image = tf.image.random_brightness(image, 32 / 255)
        image = tf.image.random_saturation(image, 0.5, 1.5)
        image = tf.cast(image, tf.float32)
        label = tf.concat([gt_text, gt_kernels, mask], axis=0)
        label = tf.transpose(label, perm=[1, 2, 0])
        label = tf.cast(label, tf.float32)
        return ({config.IMAGE: image}, label)

    def _get_all_tfrecords(self):
        return tf.data.Dataset.list_files(
            os.path.join(self.dataset_dir, "*.tfrecord")
        )

    def build(self):
        files = self._get_all_tfrecords()

        dataset = files.interleave(
            tf.data.TFRecordDataset,
            cycle_length=self.num_readers,
            num_parallel_calls=self.num_readers,
        )
        dataset = dataset.map(
            self._parse_example, num_parallel_calls=self.num_readers
        )
        dataset = dataset.map(
            self._preprocess_example, num_parallel_calls=self.num_readers
        )

        if self.should_shuffle:
            dataset = dataset.shuffle(buffer_size=config.SHUFFLE_BUFFER_SIZE)

        if self.should_repeat:
            dataset = dataset.repeat()  # Repeat forever for training.
        else:
            dataset = dataset.repeat(1)
        dataset = dataset.padded_batch(
            self.batch_size,
            padded_shapes=(
                {config.IMAGE: [None, None, 3]},
                [None, None, config.KERNEL_NUM + 1],
            ),
        ).prefetch(self.batch_size)
        return dataset
