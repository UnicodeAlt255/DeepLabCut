"""
DeepLabCut Toolbox
https://github.com/AlexEMG/DeepLabCut
A Mathis, alexander.mathis@bethgelab.org
T Nath, nath@rowland.harvard.edu
M Mathis, mackenzie@post.harvard.edu

"""

####################################################
# Dependencies
####################################################

import os.path
from deeplabcut.pose_estimation_tensorflow.nnet import predict
from deeplabcut.pose_estimation_tensorflow.config import load_config
from deeplabcut.pose_estimation_tensorflow.dataset.pose_dataset import data_to_input

from random import sample
import time
import pandas as pd
import numpy as np
import os
import argparse
from pathlib import Path
from tqdm import tqdm
import tensorflow as tf
from deeplabcut.utils import auxiliaryfunctions
import cv2
from skimage.util import img_as_ubyte

####################################################
# Loading data, and defining model folder
####################################################

def analyze_videos(config,videos,shuffle=1,trainingsetindex=0,videotype='avi',gputouse=None,save_as_csv=False, destfolder=None):
    """
    Makes prediction based on a trained network. The index of the trained network is specified by parameters in the config file (in particular the variable `snapshotindex`)

    You can crop the frames (before analysis), by changing 'cropping'=True and setting 'x1','x2','y1','y2' in the config file.
    
    Output: The labels are stored as a MultiIndex Pandas Array, which contains the name of the network, body part name, (x, y) label position
    in pixels, and the likelihood for each frame per body part. These arrays are stored in an efficient Hierarchical Data Format (HDF)
    in the same directory, where the video is stored. However, if the flag save_as_csv is set to True, the data can also be exported in
    comma-separated values format (.csv), which in turn can be imported in many programs, such as MATLAB, R, Prism, etc.
    
    Parameters
    ----------
    config : string
        Full path of the config.yaml file.
    videos : list of string
        List of full paths to videos or directories of videos (of a specific extension) for analysis.
    shuffle : int, optional
        Shuffle index of the training dataset used for training the network (default 1).
    trainingsetindex : int, optional
        Which TrainingsetFraction to use (default 1).
        Note that TrainingFraction is a list in config.yaml.
    videotype : string, optional
       Extension of videos for directories in the `videos` parameter (default ".avi").
       Only videos of this extension are analyzed within the directories.
    gputouse : int or None, optional
        Number of your GPU (default None).
        nvidia-smi can be used to view the GPU number.
        See: https://nvidia.custhelp.com/app/answers/detail/a_id/3751/~/useful-nvidia-smi-queries
    save_as_csv : bool, optional
        Whether to save the predictions in a .csv file (default False).

    destfolder: string, optional
        Specifies the destination folder for analysis data (default is the path of the video)

    Examples
    --------
    If you want to analyze only 1 video:

    >>> deeplabcut.analyze_videos('/analysis/project/reaching-task/config.yaml',['/analysis/project/videos/reachingvideo1.avi'])

    If you want to analyze all videos of type avi in a folder:

    >>> deeplabcut.analyze_videos('/analysis/project/reaching-task/config.yaml',['/analysis/project/videos'],videotype='.avi')

    If you want to analyze multiple videos:

    >>> deeplabcut.analyze_videos('/analysis/project/reaching-task/config.yaml',['/analysis/project/videos/reachingvideo1.avi','/analysis/project/videos/reachingvideo2.avi'])

    If you want to analyze multiple videos with shuffle = 2:

    >>> deeplabcut.analyze_videos('/analysis/project/reaching-task/config.yaml',['/analysis/project/videos/reachingvideo1.avi','/analysis/project/videos/reachingvideo2.avi'], shuffle=2)

    If you want to analyze multiple videos with shuffle = 2 and save results as an additional csv file too:

    >>> deeplabcut.analyze_videos('/analysis/project/reaching-task/config.yaml',['/analysis/project/videos/reachingvideo1.avi','/analysis/project/videos/reachingvideo2.avi'], shuffle=2,save_as_csv=True)

    """
    if 'TF_CUDNN_USE_AUTOTUNE' in os.environ:
        del os.environ['TF_CUDNN_USE_AUTOTUNE'] #was potentially set during training

    tf.reset_default_graph()
    start_path=os.getcwd() #record cwd to return to this directory in the end

    cfg = auxiliaryfunctions.read_config(config)
    trainFraction = cfg['TrainingFraction'][trainingsetindex]
    modelfolder=os.path.join(cfg["project_path"],str(auxiliaryfunctions.GetModelFolder(trainFraction,shuffle,cfg)))
    path_test_config = Path(modelfolder) / 'test' / 'pose_cfg.yaml'
    try:
        dlc_cfg = load_config(str(path_test_config))
    except FileNotFoundError:
        raise FileNotFoundError("It seems the model for shuffle %s and trainFraction %s does not exist."%(shuffle,trainFraction))

    # Check which snapshots are available and sort them by # iterations
    try:
      Snapshots = np.array([fn.split('.')[0]for fn in os.listdir(os.path.join(modelfolder , 'train'))if "index" in fn])
    except FileNotFoundError:
      raise FileNotFoundError("Snapshots not found! It seems the dataset for shuffle %s has not been trained/does not exist.\n Please train it before using it to analyze videos.\n Use the function 'train_network' to train the network for shuffle %s."%(shuffle,shuffle))

    if cfg['snapshotindex'] == 'all':
        print("Snapshotindex is set to 'all' in the config.yaml file. Running video analysis with all snapshots is very costly! Use the function 'evaluate_network' to choose the best the snapshot. For now, changing snapshot index to -1!")
        snapshotindex = -1
    else:
        snapshotindex=cfg['snapshotindex']

    increasing_indices = np.argsort([int(m.split('-')[1]) for m in Snapshots])
    Snapshots = Snapshots[increasing_indices]

    print("Using %s" % Snapshots[snapshotindex], "for model", modelfolder)

    ##################################################
    # Load and setup CNN part detector
    ##################################################

    # Check if data already was generated:
    dlc_cfg['init_weights'] = os.path.join(modelfolder , 'train', Snapshots[snapshotindex])
    trainingsiterations = (dlc_cfg['init_weights'].split(os.sep)[-1]).split('-')[-1]

    #update batchsize (based on parameters in config.yaml)
    dlc_cfg['batch_size']=cfg['batch_size']
    # Name for scorer:
    DLCscorer = auxiliaryfunctions.GetScorerName(cfg,shuffle,trainFraction,trainingsiterations=trainingsiterations)
    sess, inputs, outputs = predict.setup_pose_prediction(dlc_cfg)
    pdindex = pd.MultiIndex.from_product([[DLCscorer], dlc_cfg['all_joints_names'], ['x', 'y', 'likelihood']],names=['scorer', 'bodyparts', 'coords'])

    if gputouse is not None: #gpu selectinon
            os.environ['CUDA_VISIBLE_DEVICES'] = str(gputouse)

    ##################################################
    # Datafolder
    ##################################################
    #checks if input is a directory
    if [os.path.isdir(i) for i in videos] == [True]:#os.path.isdir(video)==True:
        """
        Analyzes all the videos in the directory.
        """
        print("Analyzing all the videos in the directory")
        videofolder= videos[0]
        os.chdir(videofolder)
        videolist=[fn for fn in os.listdir(os.curdir) if (videotype in fn) and ('_labeled.mp4' not in fn)] #exclude labeled-videos!
        Videos = sample(videolist,len(videolist)) # this is useful so multiple nets can be used to analzye simultanously
    else:
        if isinstance(videos,str):
            if os.path.isfile(videos): # #or just one direct path!
                Videos=[videos]
            else:
                Videos=[]
        else:
            Videos=[v for v in videos if os.path.isfile(v)]

    if len(Videos)>0:
        #looping over videos
        for video in Videos:
            AnalyzeVideo(video,DLCscorer,cfg,dlc_cfg,sess,inputs, outputs,pdindex,save_as_csv, destfolder)

    os.chdir(str(start_path))
    print("The videos are analyzed. Now your research can truly start! \n You can create labeled videos with 'create_labeled_video'.")
    print("If the tracking is not satisfactory for some videos, consider expanding the training set. You can use the function 'extract_outlier_frames' to extract any outlier frames!")

def GetPoseF(cfg,dlc_cfg, sess, inputs, outputs,cap,nframes,batchsize):
    ''' Batchwise prediction of pose '''

    PredicteData = np.zeros((nframes, 3 * len(dlc_cfg['all_joints_names'])))
    batch_ind = 0 # keeps track of which image within a batch should be written to
    batch_num = 0 # keeps track of which batch you are at
    ny,nx=int(cap.get(4)),int(cap.get(3))
    if cfg['cropping']:
        print("Cropping based on the x1 = %s x2 = %s y1 = %s y2 = %s. You can adjust the cropping coordinates in the config.yaml file." %(cfg['x1'], cfg['x2'],cfg['y1'], cfg['y2']))
        nx=cfg['x2']-cfg['x1']
        ny=cfg['y2']-cfg['y1']
        if nx>0 and ny>0:
            pass
        else:
            raise Exception('Please check the order of cropping parameter!')
        if cfg['x1']>=0 and cfg['x2']<int(cap.get(3)+1) and cfg['y1']>=0 and cfg['y2']<int(cap.get(4)+1):
            pass #good cropping box
        else:
            raise Exception('Please check the boundary of cropping!')

    frames = np.empty((batchsize, ny, nx, 3), dtype='ubyte') # this keeps all frames in a batch
    pbar=tqdm(total=nframes)
    counter=0
    step=max(10,int(nframes/100))
    while(cap.isOpened()):
            if counter%step==0:
                pbar.update(step)
            ret, frame = cap.read()
            if ret:
                frame=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                if cfg['cropping']:
                    frames[batch_ind] = img_as_ubyte(frame[cfg['y1']:cfg['y2'],cfg['x1']:cfg['x2']])
                else:
                    frames[batch_ind] = img_as_ubyte(frame)

                if batch_ind==batchsize-1:
                    pose = predict.getposeNP(frames,dlc_cfg, sess, inputs, outputs)
                    PredicteData[batch_num*batchsize:(batch_num+1)*batchsize, :] = pose
                    batch_ind = 0
                    batch_num += 1
                else:
                   batch_ind+=1
            else:
                nframes = counter
                print("Detected frames: ", nframes)
                if batch_ind>0:
                    pose = predict.getposeNP(frames, dlc_cfg, sess, inputs, outputs) #process the whole batch (some frames might be from previous batch!)
                    PredicteData[batch_num*batchsize:batch_num*batchsize+batch_ind, :] = pose[:batch_ind,:]
                break
            counter+=1

    return PredicteData,nframes

def GetPoseS(cfg,dlc_cfg, sess, inputs, outputs,cap,nframes):
    ''' Non batch wise pose estimation for video cap.'''
    if cfg['cropping']:
        print("Cropping based on the x1 = %s x2 = %s y1 = %s y2 = %s. You can adjust the cropping coordinates in the config.yaml file." %(cfg['x1'], cfg['x2'],cfg['y1'], cfg['y2']))
        nx=cfg['x2']-cfg['x1']
        ny=cfg['y2']-cfg['y1']
        if nx>0 and ny>0:
            pass
        else:
            raise Exception('Please check the order of cropping parameter!')
        if cfg['x1']>=0 and cfg['x2']<int(cap.get(3)+1) and cfg['y1']>=0 and cfg['y2']<int(cap.get(4)+1):
            pass #good cropping box
        else:
            raise Exception('Please check the boundary of cropping!')

    PredicteData = np.zeros((nframes, 3 * len(dlc_cfg['all_joints_names'])))
    pbar=tqdm(total=nframes)
    counter=0
    step=max(10,int(nframes/100))
    while(cap.isOpened()):
            if counter%step==0:
                pbar.update(step)

            ret, frame = cap.read()
            if ret:
                frame=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                if cfg['cropping']:
                    frame= img_as_ubyte(frame[cfg['y1']:cfg['y2'],cfg['x1']:cfg['x2']])
                else:
                    frame = img_as_ubyte(frame)
                pose = predict.getpose(frame, dlc_cfg, sess, inputs, outputs)
                PredicteData[counter, :] = pose.flatten()  # NOTE: thereby cfg['all_joints_names'] should be same order as bodyparts!
            else:
                nframes=counter
                break
            counter+=1

    pbar.close()
    return PredicteData,nframes


def AnalyzeVideo(video,DLCscorer,cfg,dlc_cfg,sess,inputs, outputs,pdindex,save_as_csv, destfolder=None):
    ''' Helper function for analyzing a video '''
    print("Starting to analyze % ", video)
    vname = Path(video).stem
    if destfolder is None:
        destfolder = str(Path(video).parents[0])
    dataname = os.path.join(destfolder,vname + DLCscorer + '.h5')
    try:
        # Attempt to load data...
        pd.read_hdf(dataname)
        print("Video already analyzed!", dataname)
    except FileNotFoundError:
        print("Loading ", video)
        cap=cv2.VideoCapture(video)

        fps = cap.get(5) #https://docs.opencv.org/2.4/modules/highgui/doc/reading_and_writing_images_and_video.html#videocapture-get
        nframes = int(cap.get(7))
        duration=nframes*1./fps
        size=(int(cap.get(4)),int(cap.get(3)))

        ny,nx=size
        print("Duration of video [s]: ", round(duration,2), ", recorded with ", round(fps,2),"fps!")
        print("Overall # of frames: ", nframes," found with (before cropping) frame dimensions: ", nx,ny)
        start = time.time()

        print("Starting to extract posture")
        if int(dlc_cfg["batch_size"])>1:
            PredicteData,nframes=GetPoseF(cfg,dlc_cfg, sess, inputs, outputs,cap,nframes,int(dlc_cfg["batch_size"]))
        else:
            PredicteData,nframes=GetPoseS(cfg,dlc_cfg, sess, inputs, outputs,cap,nframes)

        stop = time.time()

        if cfg['cropping']==True:
            coords=[cfg['x1'],cfg['x2'],cfg['y1'],cfg['y2']]
        else:
            coords=[0, nx, 0, ny]

        dictionary = {
            "start": start,
            "stop": stop,
            "run_duration": stop - start,
            "Scorer": DLCscorer,
            "config file": dlc_cfg,
            "fps": fps,
            "batch_size": dlc_cfg["batch_size"],
            "frame_dimensions": (ny, nx),
            "nframes": nframes,
            "cropping": cfg['cropping'],
            "cropping_parameters": coords
        }
        metadata = {'data': dictionary}

        print("Saving results in %s..." %(Path(video).parents[0]))
        auxiliaryfunctions.SaveData(PredicteData[:nframes,:], metadata, dataname, pdindex, range(nframes),save_as_csv)

def GetPosesofFrames(cfg,dlc_cfg, sess, inputs, outputs,directory,framelist,nframes,batchsize):
    ''' Batchwise prediction of pose  for framelist in directory'''
    from skimage import io
    print("Starting to extract posture")
    im=io.imread(os.path.join(directory,framelist[0]),mode='RGB')
    ny,nx,nc=np.shape(im)
    print("Overall # of frames: ", nframes," found with (before cropping) frame dimensions: ", nx,ny)

    PredicteData = np.zeros((nframes, 3 * len(dlc_cfg['all_joints_names'])))
    batch_ind = 0 # keeps track of which image within a batch should be written to
    batch_num = 0 # keeps track of which batch you are at

    if cfg['cropping']:
        print("Cropping based on the x1 = %s x2 = %s y1 = %s y2 = %s. You can adjust the cropping coordinates in the config.yaml file." %(cfg['x1'], cfg['x2'],cfg['y1'], cfg['y2']))
        nx,ny=cfg['x2']-cfg['x1'],cfg['y2']-cfg['y1']
        if nx>0 and ny>0:
            pass
        else:
            raise Exception('Please check the order of cropping parameter!')
        if cfg['x1']>=0 and cfg['x2']<int(np.shape(im)[1]) and cfg['y1']>=0 and cfg['y2']<int(np.shape(im)[0]):
            pass #good cropping box
        else:
            raise Exception('Please check the boundary of cropping!')

    pbar=tqdm(total=nframes)
    counter=0
    step=max(10,int(nframes/100))

    if batchsize==1:
        for counter,framename in enumerate(framelist):
                frame=io.imread(os.path.join(directory,framename),mode='RGB')
                if counter%step==0:
                    pbar.update(step)

                if cfg['cropping']:
                    frame= img_as_ubyte(frame[cfg['y1']:cfg['y2'],cfg['x1']:cfg['x2'],:])
                else:
                    frame = img_as_ubyte(frame)

                pose = predict.getpose(frame, dlc_cfg, sess, inputs, outputs)
                PredicteData[counter, :] = pose.flatten()
    else:
        frames = np.empty((batchsize, ny, nx, 3), dtype='ubyte') # this keeps all the frames of a batch
        for counter,framename in enumerate(framelist):
                frame=io.imread(os.path.join(directory,framename),mode='RGB')
                if counter%step==0:
                    pbar.update(step)

                if cfg['cropping']:
                    frames[batch_ind] = img_as_ubyte(frame[cfg['y1']:cfg['y2'],cfg['x1']:cfg['x2'],:])
                else:
                    frames[batch_ind] = img_as_ubyte(frame)

                if batch_ind==batchsize-1:
                    pose = predict.getposeNP(frames,dlc_cfg, sess, inputs, outputs)
                    PredicteData[batch_num*batchsize:(batch_num+1)*batchsize, :] = pose
                    batch_ind = 0
                    batch_num += 1
                else:
                   batch_ind+=1

        if batch_ind>0: #take care of the last frames (the batch that might have been processed)
            pose = predict.getposeNP(frames, dlc_cfg, sess, inputs, outputs) #process the whole batch (some frames might be from previous batch!)
            PredicteData[batch_num*batchsize:batch_num*batchsize+batch_ind, :] = pose[:batch_ind,:]

    pbar.close()
    return PredicteData,nframes,nx,ny


def analyze_time_lapse_frames(config,directory,frametype='.png',shuffle=1,trainingsetindex=0,gputouse=None,save_as_csv=False):
    """
    Analyze all images (of type = frametype) in a folder and store the output in one file.

    You can crop the frames (before analysis), by changing 'cropping'=True and setting 'x1','x2','y1','y2' in the config file.

    Output: The labels are stored as a MultiIndex Pandas Array, which contains the name of the network, body part name, (x, y) label position
    in pixels, and the likelihood for each frame per body part. These arrays are stored in an efficient Hierarchical Data Format (HDF)
    in the same directory, where the video is stored. However, if the flag save_as_csv is set to True, the data can also be exported in
    comma-separated values format (.csv), which in turn can be imported in many programs, such as MATLAB, R, Prism, etc.

    Parameters
    ----------
    config : string
        Full path of the config.yaml file as a string.
    directory : string
        Full path to directory containing the frames that shall be analyzed.
    frametype : string, optional
        File extension of the frames to analyze (default '.png').
    shuffle : int, optional
        Shuffle index of the training dataset used for training the network (default 1).
    trainingsetindex : int, optional
        Which TrainingsetFraction to use (default 0).
        Note that TrainingFraction is a list in config.yaml.
     gputouse : int or None, optional
        Number of your GPU (default None).
        nvidia-smi can be used to view the GPU number.
        See: https://nvidia.custhelp.com/app/answers/detail/a_id/3751/~/useful-nvidia-smi-queries
    save_as_csv : bool, optional
        Whether to save the predictions in a .csv file (default False).

    Examples
    --------
    If you want to analyze all frames in /analysis/project/timelapseexperiment1:

    >>> deeplabcut.analyze_time_lapse_frames('/analysis/project/reaching-task/config.yaml','/analysis/project/timelapseexperiment1')

    If you want to analyze all frames in /analysis/project/timelapseexperiment1:

    >>> deeplabcut.analyze_time_lapse_frames('/analysis/project/reaching-task/config.yaml','/analysis/project/timelapseexperiment1', frametype='.bmp')

    Note: for test purposes one can extract all frames from a video with ffmeg, e.g. ffmpeg -i testvideo.avi thumb%04d.png
    """

    if 'TF_CUDNN_USE_AUTOTUNE' in os.environ:
        del os.environ['TF_CUDNN_USE_AUTOTUNE'] #was potentially set during training

    tf.reset_default_graph()
    start_path=os.getcwd() #record cwd to return to this directory in the end

    cfg = auxiliaryfunctions.read_config(config)
    trainFraction = cfg['TrainingFraction'][trainingsetindex]
    modelfolder=os.path.join(cfg["project_path"],str(auxiliaryfunctions.GetModelFolder(trainFraction,shuffle,cfg)))
    path_test_config = Path(modelfolder) / 'test' / 'pose_cfg.yaml'
    try:
        dlc_cfg = load_config(str(path_test_config))
    except FileNotFoundError:
        raise FileNotFoundError("It seems the model for shuffle %s and trainFraction %s does not exist."%(shuffle,trainFraction))

    # Check which snapshots are available and sort them by # iterations
    try:
      Snapshots = np.array([fn.split('.')[0]for fn in os.listdir(os.path.join(modelfolder , 'train'))if "index" in fn])
    except FileNotFoundError:
      raise FileNotFoundError("Snapshots not found! It seems the dataset for shuffle %s has not been trained/does not exist.\n Please train it before using it to analyze videos.\n Use the function 'train_network' to train the network for shuffle %s."%(shuffle,shuffle))

    if cfg['snapshotindex'] == 'all':
        print("Snapshotindex is set to 'all' in the config.yaml file. Running video analysis with all snapshots is very costly! Use the function 'evaluate_network' to choose the best the snapshot. For now, changing snapshot index to -1!")
        snapshotindex = -1
    else:
        snapshotindex=cfg['snapshotindex']

    increasing_indices = np.argsort([int(m.split('-')[1]) for m in Snapshots])
    Snapshots = Snapshots[increasing_indices]

    print("Using %s" % Snapshots[snapshotindex], "for model", modelfolder)

    ##################################################
    # Load and setup CNN part detector
    ##################################################

    # Check if data already was generated:
    dlc_cfg['init_weights'] = os.path.join(modelfolder , 'train', Snapshots[snapshotindex])
    trainingsiterations = (dlc_cfg['init_weights'].split(os.sep)[-1]).split('-')[-1]

    #update batchsize (based on parameters in config.yaml)
    dlc_cfg['batch_size']=cfg['batch_size']

    # Name for scorer:
    DLCscorer = auxiliaryfunctions.GetScorerName(cfg,shuffle,trainFraction,trainingsiterations=trainingsiterations)
    sess, inputs, outputs = predict.setup_pose_prediction(dlc_cfg)
    pdindex = pd.MultiIndex.from_product([[DLCscorer], dlc_cfg['all_joints_names'], ['x', 'y', 'likelihood']],names=['scorer', 'bodyparts', 'coords'])

    if gputouse is not None: #gpu selectinon
            os.environ['CUDA_VISIBLE_DEVICES'] = str(gputouse)

    ##################################################
    # Loading the images
    ##################################################
    #checks if input is a directory
    if os.path.isdir(directory)==True:
        """
        Analyzes all the frames in the directory.
        """
        print("Analyzing all frames in the directory: ", directory)
        os.chdir(directory)
        framelist=np.sort([fn for fn in os.listdir(os.curdir) if (frametype in fn)])

        vname = Path(directory).stem
        dataname = os.path.join(directory,vname + DLCscorer + '.h5')
        try:
            # Attempt to load data...
            pd.read_hdf(dataname)
            print("Frames already analyzed!", dataname)
        except FileNotFoundError:
            nframes = len(framelist)
            if nframes>1:
                start = time.time()

                PredicteData,nframes,nx,ny=GetPosesofFrames(cfg,dlc_cfg, sess, inputs, outputs,directory,framelist,nframes,dlc_cfg['batch_size'])
                stop = time.time()

                if cfg['cropping']==True:
                    coords=[cfg['x1'],cfg['x2'],cfg['y1'],cfg['y2']]
                else:
                    coords=[0, nx, 0, ny]

                dictionary = {
                    "start": start,
                    "stop": stop,
                    "run_duration": stop - start,
                    "Scorer": DLCscorer,
                    "config file": dlc_cfg,
                    "batch_size": dlc_cfg["batch_size"],
                    "frame_dimensions": (ny, nx),
                    "nframes": nframes,
                    "cropping": cfg['cropping'],
                    "cropping_parameters": coords
                }
                metadata = {'data': dictionary}

                print("Saving results in %s..." %(directory))

                auxiliaryfunctions.SaveData(PredicteData[:nframes,:], metadata, dataname, pdindex, framelist,save_as_csv)
                print("The folder was analyzed. Now your research can truly start!")
                print("If the tracking is not satisfactory for some frome, consider expanding the training set.")
            else:
                print("No frames were found. Consider changing the path or the frametype.")

    os.chdir(str(start_path))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('video')
    parser.add_argument('config')
    cli_args = parser.parse_args()
