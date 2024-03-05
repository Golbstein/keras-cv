# Copyright 2024 The KerasCV Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import numpy as np
from keras import Sequential
from keras_cv.api_export import keras_cv_export
from keras_nlp.layers import RotaryEmbedding, TransformerDecoder
from keras_cv.layers import TransformerEncoder as CVTransformerEncoder
from keras_cv.models.task import Task
from keras_cv.layers.attention_pooling import AttentionPooling
from keras_cv.layers.vit_layers import PatchingAndEmbedding


@keras_cv_export(["keras_cv.models.CoCa"])
class CoCa(Task):
    def __init__(self,
                 img_patch_size=18,
                 encoder_depth=40,
                 encoder_heads=16,
                 encoder_intermediate_dim=6144,
                 encoder_width=1408,
                 unimodal_decoder_depth=18,
                 multimodal_decoder_depth=18,
                 decoder_intermediate_dim=5632,
                 unimodal_decoder_heads=16,
                 multimodal_decoder_heads=16,
                 contrastive_query_length=1,
                 captioning_query_length=256,
                 contrastive_attn_heads=16,
                 captioning_attn_heads=16,
                 contrastive_loss_weight=0.5,
                 captioning_loss_weight=0.5,
                 **kwargs):
        super().__init__(**kwargs)

        self.img_patch_size = img_patch_size

        self.encoder_depth = encoder_depth
        self.encoder_heads = encoder_heads
        self.encoder_width = encoder_width
        self.encoder_intermediate_dim = encoder_intermediate_dim

        self.unimodal_decoder_depth = unimodal_decoder_depth
        self.multimodal_decoder_depth = multimodal_decoder_depth
        self.decoder_intermediate_dim = decoder_intermediate_dim
        self.unimodal_decoder_heads = unimodal_decoder_heads
        self.multimodal_decoder_heads = multimodal_decoder_heads

        self.contrastive_query_length = contrastive_query_length
        self.contrastive_attn_heads = contrastive_attn_heads
        self.contrastive_loss_weight = contrastive_loss_weight

        self.captioning_query_length = captioning_query_length
        self.captioning_attn_heads = captioning_attn_heads
        self.captioning_loss_weight = captioning_loss_weight

        # Layer Definitions
        self.image_patching = PatchingAndEmbedding(self.encoder_width, self.img_patch_size)
        self.image_encoder = Sequential([
            CVTransformerEncoder(self.encoder_width, self.encoder_heads, self.encoder_intermediate_dim)
            for _ in range(self.encoder_depth)
        ])

        self.text_embedding = RotaryEmbedding()
        self.unimodal_text_decoder = Sequential([
            TransformerDecoder(self.decoder_intermediate_dim, self.unimodal_decoder_heads)
            for _ in range(self.unimodal_decoder_depth)
        ])
        self.multimodal_text_decoder = Sequential([
            TransformerDecoder(self.decoder_intermediate_dim, self.multimodal_decoder_heads)
            for _ in range(self.multimodal_decoder_depth)
        ])

        self.contrastive_attn_pooling = AttentionPooling(self.encoder_width, self.contrastive_attn_heads)
        self.captioning_attn_pooling = AttentionPooling(self.encoder_width, self.captioning_attn_heads)

        # These are learnable weights defined in build as per Keras recommendations
        self.cls_token = None
        self.contrastive_query = None
        self.captioning_query = None
    """ Contrastive Captioner foundational model implementation.

    This model implements the "Contrastive Captioners are image-Text Foundational Models" by Yu, et al.
    (https://arxiv.org/pdf/2205.01917.pdf). In short, the CoCa model combines the ideas of Contrastive techniques
    such as CLIP, with Generative Captioning approaches such as SimVLM.

    The architecture of clip can be described as an Image Visual Transformer Encoder in parallel to self-attention-only
    Text Transformer Decoder, the outputs of both of which are passed into a multimodal Transformer Decoder. The
    contrastive loss from the ViT and the uni-modal Text Decoder is combined with a captioning loss from the multi-modal
    Decoder in order to produce the combined total loss.

    Basic Usage:
    ```python

    images = ... # [batch_size, height, width, channel]
    text = ... # [batch_size, text_dim, sequence_length]

    coca = CoCa()

    # [batch_size, sequence_length, captioning_query_length]
    output = coca(images, text)
    ```

    All default arguments should be consistent with the original paper's details.

    Args:
        img_patch_size: N of each NxN patch generated from linearization of the input images
        encoder_depth: number of image encoder blocks
        encoder_heads: number of attention heads used in each image encoder block
        encoder_intermediate_dim: dimensionality of the encoder blocks' intermediate representation (MLP dimensionality)
        encoder_width: dimensionality of the encoder's projection, consistent with wording used in CoCa paper.
        unimodal_decoder_depth: number of decoder blocks used for text self-attention/embedding
        multimodal_decoder_depth: number of decoder blocks used for image-text cross-attention and captioning
        decoder_intermediate_dim: dimensionality of the decoder blocks' MLPs
        unimodal_decoder_heads: number of attention heads in the unimodal decoder
        multimodal_decoder_heads: number of attention heads in the multimodal decoder
        contrastive_query_length: number of tokens to use to represent contrastive query
        captioning_query_length: number of tokens to use to represent captioning query
        contrastive_attn_heads: number of attention heads used for the contrastive attention pooling
        captioning_attn_heads: number of attention heads used for the captioning attention pooling
        contrastive_loss_weight: weighting of contrastive loss
        captioning_loss_weight: weighting of captioning loss
    """

    def build(self, input_shape):
        super().build(input_shape)

        # Validate Input Shape
        if len(input_shape) < 2:
            raise ValueError("Build arguments to CoCa expected to contain shapes of both text and image data; "
                             f"got {len(input_shape)} shapes.")

        images_shape = input_shape[0]
        text_shape = input_shape[1]

        if len(images_shape) != 4:
            raise ValueError("Image shape expected to be of shape [batch_size, height, width, channels]. Instead got "
                             f"shape: {images_shape}")
        elif len(text_shape) != 2:
            raise ValueError("Text shape expected to be of shape [batch_size, context_length]. Instead got shape"
                             f": {text_shape}")

        text_dim = text_shape[1]
        batch_size = images_shape[0]
        if batch_size != text_shape[0]:
            raise ValueError(f"Differing batch sizes between images and texts input. {batch_size} vs {text_shape[0]}")

        # Build Layers
        self.image_patching.build(images_shape)

        # Add 1 for CLs token appended by patching
        num_patches = (images_shape[1] // self.img_patch_size) * (images_shape[2] // self.img_patch_size) + 1
        self.image_encoder.build((batch_size, self.encoder_width, num_patches))

        text_shape_with_cls_token = [s for s in text_shape]
        text_shape_with_cls_token[1] += 1
        self.text_embedding.build(text_shape_with_cls_token)

        self.unimodal_text_decoder.build(text_shape_with_cls_token)

        self.contrastive_attn_pooling.build((batch_size, num_patches, self.encoder_width))
        self.captioning_attn_pooling.build((batch_size, num_patches, self.encoder_width))

        self.multimodal_text_decoder.build((batch_size, self.encoder_width, self.captioning_query_length),
                                           text_shape_with_cls_token)

        # Learnable Weights
        self.cls_token = self.add_weight(shape=(batch_size, 1, text_dim), name="cls_token", trainable=True)

        self.contrastive_query = self.add_weight(shape=(batch_size, self.encoder_width, self.contrastive_query_length),
                                                 trainable=True)
        self.captioning_query = self.add_weight(shape=(batch_size, self.encoder_width, self.captioning_query_length),
                                                trainable=True)

    def call(self, images, texts):
        """
        Forward pass of the Coca Model from raw image and text data

        Args:
            images: [batch_size, height, width, channels] representing images
            texts: Tensor, typically represented as [batch_size, sequence_length, feature_length] or
                [batch_size, sequence_length, num_heads, feature_length]. The sequence_length and/or feature_length
                are required.

        Returns:
            Output: Output of the captioning Transformer Decoder with captioning cross-attention
        """
        img_encoding = self.image_patching(images) # [batch_size, encoder_width, img_patches_len+1]
        img_encoding = self.image_encoder(img_encoding)  # [batch_size, img_patches_len+1, encoder_width]

        # This is only needed for loss calculations
        # contrastive_feature = self.con_attn_pooling(self.contrastive_query, img_encoding)

        # [batch_size, encoder_width, captioning_query_length]
        captioning_feature = self.captioning_attn_pooling(self.captioning_query, img_encoding)

        # [batch_size, sequence_length+1, text_dim]
        text_tokens = np.concatenate(texts, self.cls_token)
        mask = np.concatenate((np.ones_like(texts), np.zeros_like(self.cls_token)))

        # [batch_size, sequence_length+1, text_dim]
        embed_text = self.text_embedding(text_tokens)
        unimodal_out = self.unimodal_text_decoder(embed_text, attention_mask=mask)

        # [batch_size, sequence_length, captioning_query_length], notice we remove the CLs token
        multimodal_out = self.multimodal_text_decoder(unimodal_out[:, :-1, :],
                                                      encoder_sequence=captioning_feature,
                                                      decoder_attention_mask=mask)

        return multimodal_out

    def get_config(self):
        config = super().get_config()
        config.update(
            {
                "img_patch_size": self.img_patch_size,
                "encoder_depth": self.encoder_depth,
                "encoder_heads": self.encoder_heads,
                "encoder_width": self.encoder_width,
                "encoder_intermediate_dim": self.encoder_intermediate_dim,
                "unimodal_decoder_depth": self.unimodal_decoder_depth,
                "multimodal_decoder_depth": self.multimodal_decoder_depth,
                "decoder_intermediate_dim": self.decoder_intermediate_dim,
                "unimodal_decoder_heads": self.unimodal_decoder_heads,
                "multimodal_decoder_heads": self.multimodal_decoder_heads,
                "contrastive_query_length": self.contrastive_query_length,
                "contrastive_attn_heads": self.contrastive_attn_heads,
                "contrastive_loss_weight": self.contrastive_loss_weight,
                "captioning_query_length": self.captioning_query_length,
                "captioning_attn_heads": self.captioning_attn_heads,
                "captioning_loss_weight": self.captioning_loss_weight,
            }
        )
        return config
