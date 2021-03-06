import os

import cv2
import numpy as np
import tensorflow as tf
from tensorflow.python.platform import tf_logging as logging

from psenet import config
from psenet.data import preprocess
from psenet.backbones.factory import Backbones


class RawDataset:
    def __init__(self, FLAGS):
        self.dataset_dir = FLAGS.dataset_dir
        self.batch_size = FLAGS.batch_size
        self.num_readers = FLAGS.num_readers
        self.should_shuffle = FLAGS.should_shuffle
        self.should_repeat = FLAGS.should_repeat
        self.min_scale = FLAGS.min_scale
        self.kernel_num = FLAGS.kernel_num
        self.should_augment = FLAGS.should_augment
        self.input_context = FLAGS.input_context
        self.prefetch = FLAGS.prefetch
        self.preprocess = Backbones.get_preprocessing(FLAGS.backbone_name)
        self.resize_length = FLAGS.resize_length
        self.crop_size = FLAGS.resize_length // 2

    def _parse_example(self, example_proto):
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
        parsed_features = tf.io.parse_single_example(example_proto, features)
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
        bboxes_count, num_points = np.asarray(bboxes.shape).astype("int64")[:2]
        if bboxes_count > 0:
            bboxes = np.reshape(
                bboxes * ([width, height] * 4),
                (bboxes_count, int(num_points / 2), 2),
            ).astype("int32")
            for i in range(bboxes_count):
                cv2.drawContours(gt_text, [bboxes[i]], -1, i + 1, -1)
                if tags[i] == "0":
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
            image = preprocess.random_scale(
                image,
                resize_length=self.resize_length,
                crop_size=self.crop_size,
            )
        else:
            image = preprocess.scale(image, resize_length=self.resize_length)

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
            tensors = preprocess.random_background_crop(
                tensors, crop_size=self.crop_size
            )
            image, gt_text, mask, gt_kernels = (
                tensors[0],
                tensors[1],
                tensors[2],
                tensors[3:],
            )
            image = tf.image.random_brightness(image, 32 / 255)
            image = tf.image.random_saturation(image, 0.5, 1.5)

        image = tf.cast(image, tf.float32)
        image = self.preprocess(image)
        gt_text = tf.cast(gt_text, tf.float32)
        gt_text = tf.sign(gt_text)
        gt_text = tf.cast(gt_text, tf.uint8)
        gt_text = tf.expand_dims(gt_text, axis=0)
        label = tf.concat([gt_text, gt_kernels], axis=0)
        label = tf.transpose(label, perm=[1, 2, 0])
        label = tf.cast(label, tf.float32)
        mask = tf.cast(mask, tf.float32)
        return ({config.IMAGE: image, config.MASK: mask}, label)

    def _get_all_tfrecords(self):
        return tf.data.Dataset.list_files(
            os.path.join(self.dataset_dir, "*.tfrecord")
        )

    def build(self):
        dataset = self._get_all_tfrecords()
        if self.input_context:
            dataset = dataset.shard(
                self.input_context.num_input_pipelines,
                self.input_context.input_pipeline_id,
            )
            logging.info(
                "Sharding the dataset for the pipeline {} out of {}".format(
                    self.input_context.input_pipeline_id,
                    self.input_context.num_input_pipelines,
                )
            )
        else:
            logging.info("Received no input context.")

        if self.should_repeat:
            dataset = dataset.repeat(None)
        else:
            dataset = dataset.repeat(1)

        if self.should_shuffle:
            dataset = dataset.shuffle(
                buffer_size=config.NUM_BATCHES_TO_SHUFFLE * self.batch_size + 1
            )

        dataset = dataset.interleave(
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

        dataset = dataset.filter(
            lambda inputs, labels: preprocess.check_image_validity(inputs)
        )

        dataset = dataset.padded_batch(
            self.batch_size,
            padded_shapes=(
                {config.IMAGE: [None, None, 3], config.MASK: [None, None]},
                [None, None, self.kernel_num],
            ),
        ).prefetch(self.prefetch)

        return dataset


def build(FLAGS):
    def input_fn(input_context=None):
        is_training = FLAGS.mode == tf.estimator.ModeKeys.TRAIN
        if FLAGS.augment_training_data:
            should_augment = is_training
        else:
            should_augment = False
        FLAGS.input_context = input_context
        FLAGS.should_augment = should_augment
        FLAGS.should_repeat = True
        FLAGS.dataset_dir = (
            FLAGS.training_data_dir if is_training else FLAGS.eval_data_dir
        )
        FLAGS.should_shuffle = is_training
        dataset = RawDataset(FLAGS).build()
        return dataset

    return input_fn
