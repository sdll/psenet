# NOTICE: This code is based on the work by Pavel Yakubovskiy.
# See https://github.com/qubvel/segmentation_models for details.
#
# The MIT License
#
# Copyright (c) 2018, Pavel Yakubovskiy, Sasha Illarionov
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import tensorflow as tf
from psenet.nets.common import Conv2dBn
from psenet.backbones.factory import Backbones

# ---------------------------------------------------------------------
#  Blocks
# ---------------------------------------------------------------------


def Conv3x3BnReLU(filters, use_batchnorm, name=None):
    def wrapper(input_tensor):
        return Conv2dBn(
            filters,
            kernel_size=3,
            activation="relu",
            kernel_initializer="he_uniform",
            padding="same",
            use_batchnorm=use_batchnorm,
            name=name,
        )(input_tensor)

    return wrapper


def FPNBlock(pyramid_filters, stage):
    conv0_name = "fpn_stage_p{}_pre_conv".format(stage)
    conv1_name = "fpn_stage_p{}_conv".format(stage)
    add_name = "fpn_stage_p{}_add".format(stage)
    up_name = "fpn_stage_p{}_upsampling".format(stage)

    channels_axis = (
        3 if tf.keras.backend.image_data_format() == "channels_last" else 1
    )

    def wrapper(input_tensor, skip):
        # if input tensor channels not equal to pyramid channels
        # we will not be able to sum input tensor and skip
        # so add extra conv layer to transform it
        input_filters = tf.keras.backend.int_shape(input_tensor)[channels_axis]
        if input_filters != pyramid_filters:
            input_tensor = tf.keras.layers.Conv2D(
                filters=pyramid_filters,
                kernel_size=(1, 1),
                kernel_initializer="he_uniform",
                name=conv0_name,
            )(input_tensor)

        skip = tf.keras.layers.Conv2D(
            filters=pyramid_filters,
            kernel_size=(1, 1),
            kernel_initializer="he_uniform",
            name=conv1_name,
        )(skip)

        x = tf.keras.layers.UpSampling2D((2, 2), name=up_name)(input_tensor)
        x = tf.keras.layers.Add(name=add_name)([x, skip])

        return x

    return wrapper


# ---------------------------------------------------------------------
#  FPN Decoder
# ---------------------------------------------------------------------


def build_fpn(
    backbone,
    skip_connection_layers,
    pyramid_filters=256,
    segmentation_filters=128,
    classes=1,
    activation="sigmoid",
    use_batchnorm=True,
    aggregation="sum",
    dropout=None,
):
    input_ = backbone.input
    x = backbone.output

    # building decoder blocks with skip connections
    skips = [
        backbone.get_layer(name=i).output
        if isinstance(i, str)
        else backbone.get_layer(index=i).output
        for i in skip_connection_layers
    ]

    # build FPN pyramid
    p5 = FPNBlock(pyramid_filters, stage=5)(x, skips[0])
    p4 = FPNBlock(pyramid_filters, stage=4)(p5, skips[1])
    p3 = FPNBlock(pyramid_filters, stage=3)(p4, skips[2])
    p2 = FPNBlock(pyramid_filters, stage=2)(p3, skips[3])

    # upsampling to same resolution
    s5 = tf.keras.layers.UpSampling2D(
        (8, 8), interpolation="nearest", name="upsampling_stage5"
    )(p5)

    s4 = tf.keras.layers.UpSampling2D(
        (4, 4), interpolation="nearest", name="upsampling_stage4"
    )(p4)

    s3 = tf.keras.layers.UpSampling2D(
        (2, 2), interpolation="nearest", name="upsampling_stage3"
    )(p3)

    s2 = p2

    # aggregating results
    if aggregation == "sum":
        x = tf.keras.layers.Add(name="aggregation_sum")([s2, s3, s4, s5])
    elif aggregation == "concat":
        concat_axis = (
            3 if tf.keras.backend.image_data_format() == "channels_last" else 1
        )
        x = tf.keras.layers.Concatenate(
            axis=concat_axis, name="aggregation_concat"
        )([s2, s3, s4, s5])
    else:
        raise ValueError(
            'Aggregation parameter should be in ("sum", "concat"), '
            "got {}".format(aggregation)
        )

    if dropout:
        x = tf.keras.layers.SpatialDropout2D(dropout, name="pyramid_dropout")(
            x
        )

    # final stage
    x = Conv3x3BnReLU(segmentation_filters, use_batchnorm, name="final_stage")(
        x
    )
    x = tf.keras.layers.UpSampling2D(
        size=(2, 2), interpolation="bilinear", name="final_upsampling"
    )(x)

    # model head (define number of output classes)
    x = tf.keras.layers.Conv2D(
        filters=classes,
        kernel_size=(3, 3),
        padding="same",
        use_bias=True,
        kernel_initializer="glorot_uniform",
        name="head_conv",
    )(x)
    x = tf.keras.layers.Activation(activation, name=activation)(x)

    # create keras model instance
    model = tf.keras.Model(input_, x)

    return model


# ---------------------------------------------------------------------
#  FPN Model
# ---------------------------------------------------------------------


def FPN(
    backbone_name="vgg16",
    input_shape=(None, None, 3),
    classes=21,
    activation="softmax",
    weights=None,
    encoder_weights="imagenet",
    encoder_freeze=False,
    encoder_features="default",
    pyramid_block_filters=256,
    pyramid_use_batchnorm=True,
    pyramid_aggregation="concat",
    pyramid_dropout=None,
    **kwargs
):
    backbone = Backbones.get_backbone(
        backbone_name,
        input_shape=input_shape,
        weights=encoder_weights,
        include_top=False,
        **kwargs
    )

    if encoder_features == "default":
        encoder_features = Backbones.get_feature_layers(backbone_name, n=4)

    model = build_fpn(
        backbone=backbone,
        skip_connection_layers=encoder_features,
        pyramid_filters=pyramid_block_filters,
        segmentation_filters=pyramid_block_filters // 2,
        use_batchnorm=pyramid_use_batchnorm,
        dropout=pyramid_dropout,
        activation=activation,
        classes=classes,
        aggregation=pyramid_aggregation,
    )

    # loading model weights
    if weights is not None:
        model.load_weights(weights)

    return model
