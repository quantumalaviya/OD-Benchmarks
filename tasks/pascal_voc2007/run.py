import keras_cv
import ml_experiments
import tensorflow as tf
import sys
import augmenters
import loader
import resource
from absl import flags
from absl import app
from tensorflow import keras


image_size = (640, 640, 3)

low, high = resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE, (high, high))


def unpackage_dict_format(inputs):
    return inputs['images'], inputs['bounding_boxes']


def load_datasets(config):
    train_ds = loader.load(
        "train", bounding_box_format="xywh"
    )
    eval_ds = loader.load(
        "test", bounding_box_format="xywh"
    )

    augmenter = augmenters.get(config.augmenter)
    inference_resizing = keras_cv.layers.Resizing(
        640, 640, bounding_box_format="xywh", pad_to_aspect_ratio=True
    )

    if config.batch_augment:
        train_ds = train_ds.apply(
            tf.data.experimental.dense_to_ragged_batch(config.batch_size)
        )
        train_ds = train_ds.map(
            lambda x: augmenter(x, training=True), num_parallel_calls=tf.data.AUTOTUNE
        )
    else:
        train_ds = train_ds.map(
            lambda x: augmenter(x), num_parallel_calls=tf.data.AUTOTUNE
        )
        train_ds = train_ds.apply(
            tf.data.experimental.dense_to_ragged_batch(config.batch_size)
        )

    eval_ds = eval_ds.apply(tf.data.experimental.dense_to_ragged_batch(config.batch_size))
    eval_ds = eval_ds.map(inference_resizing, num_parallel_calls=tf.data.AUTOTUNE)

    train_ds = train_ds.map(unpackage_dict_format, num_parallel_calls=tf.data.AUTOTUNE)
    eval_ds = eval_ds.map(unpackage_dict_format, num_parallel_calls=tf.data.AUTOTUNE)
    return train_ds, eval_ds


def get_backbone(config):
    if config.backbone == "keras.applications.ResNet50/imagenet":
        inputs = keras.layers.Input(shape=image_size)
        x = inputs
        x = keras.applications.resnet.preprocess_input(x)

        backbone = keras.applications.ResNet50(
            include_top=False, input_tensor=x, weights="imagenet"
        )

        c3_output, c4_output, c5_output = [
            backbone.get_layer(layer_name).output
            for layer_name in [
                "conv3_block4_out",
                "conv4_block6_out",
                "conv5_block3_out",
            ]
        ]
        return keras.Model(inputs=inputs, outputs=[c3_output, c4_output, c5_output])
    if config.backbone == "keras_cv.models.ResNet50/imagenet":
        return (
            keras_cv.models.ResNet50(
                include_top=False, weights="imagenet", include_rescaling=True
            ).as_backbone(),
        )
    raise ValueError(f"Invalid backbone, received backbone={config.backbone}")


def get_model(config):
    model = keras_cv.models.RetinaNet(
        classes=20,
        bounding_box_format="xywh",
        backbone=get_backbone(config),
    )
    model.backbone.trainable = True#config.backbone_trainable
    return model


def get_name(config):
    return f'{config.backbone}-{config.augmenter}'

def run(config):
    train_ds, eval_ds = load_datasets(config)
    model = get_model(config)

    optimizer = tf.optimizers.SGD(global_clipnorm=10.0)
    model.compile(
        classification_loss="focal",
        box_loss="smoothl1",
        optimizer=optimizer,
    )

    history = model.fit(
        train_ds.take(1),
        validation_data=eval_ds.take(1),
        epochs=5,
        # callbacks=callbacks,
    )
    # metrics = model.evaluate(eval_ds.take(1), return_dict=True)
    return ml_experiments.Result(
        # Must be generated for sweeps
        name=get_name(config),
        artifacts=[
            ml_experiments.artifacts.KerasHistory(history, name="fit_history"),
        ],
    )
