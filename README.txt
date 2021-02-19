This repository contains two models to generate images with different attributes.

More models are adding...

StarGAN-v2
This model is kind of a style-transfer model. The model takes a source image and a reference image at the same time, and it will generate a new image with the same object as the ref image and the the pose as the source image. They provide images from three domains (cat, dog, and wild animals). Image from any domain can be the source/ref image.
Based on the code from https://github.com/clovaai/stargan-v2
Requirements: 
pytorch==1.4.0 
torchvision==0.5.0 
ffmpeg-python==0.2.0 
opencv-python==4.1.2.30 
scikit-image==0.16.2 
pillow==7.0.0 
scipy==1.2.1 
tqdm==4.42.0 
munch==2.5.0
How to run:
1. Download the dataset and pre-trained model
bash download.sh afhq-dataset
bash download.sh pretrained-network-afhq
The dataset will be downloaded to /data folder
2. Select source and ref images from the dataset and place them at /assets/representative/afhq/ref (source)
3. Run the model with bash sample.sh
4. Find results from /expr/results/afhq

DiscoFaceGAN
This model can generate human face images with different pose/expression/lightings
Based on the code from https://github.com/microsoft/DiscoFaceGAN
Requirements: Linux System; Python 3.6 with numpy 1.14.3 or newer; Tensorfloew 1.12 with GPU
How to Run: bash samples.sh
Parameters to change: 
--factor 0 -> all the three conditions (pose, expression,lighting) 1 -> expression 2 -> lighting 3 -> pose
--subject 50 -> generate images with 50 identities 
--variation -> generate 10 images per subject
The images will be saved at the folder generated_images

If you have any problem, contact zbao@andrew.cmu.edu (Zhipeng)
