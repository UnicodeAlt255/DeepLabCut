#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Nov  5 18:06:13 2018

@author: alex

This is a test script that should achieve a certain performance on your system.
See values on our system, the DLC 2.0 docker with TF 1.8 on a NVIDIA GTX 1080Ti.
https://github.com/MMathisLab/Docker4DeepLabCut2.0

This test trains on the open field data set for about 30 minutes (15k iterations).

The results will be something like this:

Results for 15001  training iterations: 95 1 train error: 2.89 pixels. Test error: 2.81  pixels.
With pcutoff of 0.1  train error: 2.89 pixels. Test error: 2.81 pixels

The analysis of the video takes 41 seconds (batch size 32) and creating the frames 8 seconds (+ a few seconds for ffmpeg) to create the video.
"""

# Importing the toolbox (takes several seconds)
import deeplabcut
import os, yaml
from pathlib import Path
import ruamel.yaml

def read_config(configname):
    """
    Reads config file

    """
    ruamelFile = ruamel.yaml.YAML()
    path = Path(configname)
    cfg = ruamelFile.load(path)
    return(cfg)

def write_config(configname,cfg):
    with open(configname, 'w') as cf:
        ruamelFile = ruamel.yaml.YAML()
        ruamelFile.dump(cfg, cf)

# Loading example data set
path_config_file = os.path.join(os.getcwd(),'openfield-Pranav-2018-10-30/config.yaml')
deeplabcut.load_demo_data(path_config_file)

cfg=read_config(path_config_file)
posefile=os.path.join(cfg['project_path'],'dlc-models/iteration-'+str(cfg['iteration'])+'/'+ cfg['Task'] + cfg['date'] + '-trainset' + str(int(cfg['TrainingFraction'][0] * 100)) + 'shuffle' + str(1),'train/pose_cfg.yaml')
DLC_config=read_config(posefile)
DLC_config['save_iters']=10
DLC_config['display_iters']=2
DLC_config['multi_step']=[[0.005,15001]]
write_config(posefile,DLC_config)

print("TRAIN NETWORK")

deeplabcut.train_network(path_config_file, shuffle=1,saveiters=15000,displayiters=100)

print("EVALUATE")
deeplabcut.evaluate_network(path_config_file,plotting=False)


print("Analyze Video")
videofile_path = os.path.join(os.getcwd(),'openfield-Pranav-2018-10-30','videos','m3v1mp4.mp4')
deeplabcut.analyze_videos(path_config_file,[videofile_path])

print("Create Labeled Video")
deeplabcut.create_labeled_video(path_config_file,[videofile_path],save_frames=True)
