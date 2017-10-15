#!/usr/bin/env python

import time
from  configparser import RawConfigParser
import os
import threading
import logging
import dateutil.parser
import dateutil.tz
import tempfile
from PIL import Image
import pyinotify
from queue import Queue
import external
import re
import shutil
from pymediainfo import MediaInfo
import jsonpath
import json

class VideoInfo(object):
    def __init__(self, source_file):
        self.json = json.loads(MediaInfo.parse(source_file).to_json())

    def general(self, prop):
        return self.get("$.tracks[?(@.track_type == 'General')]." + prop)
    
    def video(self, prop):
        return self.get("$.tracks[?(@.track_type == 'Video')]." + prop)

    def get(self, jpath):
        val = jsonpath.jsonpath(
            self.json,
            jpath)
        return val[0] if val and len(val) else False 


def validate_requirement(application):
    try:
        external.call('which', application).assert_status(0)
    except external.Error:
        logging.error('missing required application: ' + application)
        raise Exception('missing required application: ' + application)


class ImageMover(pyinotify.ProcessEvent):
    def __init__(self):


        self.path = os.path.dirname(os.path.realpath(__file__))
        self._parse_config(os.path.join(self.path, 'image_mover.cfg'))
        logging.basicConfig(
            filename=os.path.join(self.path, self.log_file),
            level=int(self.log_level),
            format='%(asctime)s %(message)s'
            )
        logging.info('Application started')
        self._expand_home_directory_in_config()
        self.lock = threading.Lock()
        self.queue = Queue()

    def _parse_config(self, config_file_path):
        parser = RawConfigParser()
        parser.read(config_file_path)
        options = parser.options('Options')
        for option in options:
            self.__dict__[option] = parser.get('Options', option)

    def _expand_home_directory_in_config(self):
        home_path = os.path.expanduser('~')
        self.from_path = self.from_path.replace('~', home_path)
        self.to_path = self.to_path.replace('~', home_path)

    def start(self):
        validate_requirement('ffmpeg')
        validate_requirement('mediainfo')
        if not os.path.isdir(self.from_path):
            raise Exception('From directory, %s, not found' % self.from_path)
        if not os.path.isdir(self.to_path):
            raise Exception('To directory, %s, not found' % self.to_path)
        self._start_observing_from_directory()

    def _start_observing_from_directory(self):
        wm = pyinotify.WatchManager()
        mask = (pyinotify.IN_CLOSE_WRITE |
                pyinotify.IN_CLOSE_NOWRITE |
                pyinotify.IN_MOVED_TO)
        notifier = pyinotify.ThreadedNotifier(wm, self)
        notifier.start()
        wm.add_watch(self.from_path, mask, rec=False)
        try:
            while True:
                if not self.queue.empty():
                    self._move_file(self.queue.get())
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info('Application ending by request from user')
        notifier.stop()

    def process_IN_MOVED_TO(self, event):
        logging.debug('File moved to observed folder')
        self.queue.put(os.path.join(event.path, event.name))

    def process_IN_CLOSE_WRITE(self, event):
        logging.debug('File copied to observed folder')
        self.queue.put(os.path.join(event.path, event.name))

    def _move_file(self, source_file):
        try:
            time.sleep(1)
            with self.lock:
                file_name, extension = os.path.splitext(source_file)
                new_name = None
                if extension.lower() == '.mp4':
                    new_name = self._get_new_name_from_video_metadata(source_file)
                elif extension.lower() == '.mov' or extension.lower() == '.3gp':
                    converted_name = '{}.mp4'.format(file_name)
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as temp_file:
                        external.call(
                            'ffmpeg',
                            '-i',
                            source_file,
                            '-vcodec',
                            'copy',
                            '-acodec',
                            'copy',
                            '-map_metadata',
                            '0',
                            '-y',
                            temp_file.name
                            ).assert_status(0)
                        shutil.move(temp_file.name, converted_name)
                        os.remove(source_file)
                    return
                else:
                    new_name = self._get_new_name_from_exif(source_file)
                if not new_name:
                    new_name = os.path.basename(source_file)
                    destination = os.path.join(self.from_path, 'failed', new_name)
                else:
                    destination = os.path.join(self.to_path, new_name)
                if not os.path.isdir(os.path.dirname(destination)):
                    os.makedirs(os.path.dirname(destination))
                if os.path.isfile('{}{}'.format(destination, extension)):
                    raise Exception("Desitnation file, {}{}, already exists, could not rename {}".format(destination, extension, source_file))
                if extension.lower() == '.mp4':
                    logging.debug("Creating low res version of %s", source_file)
                    size = _get_video_size(source_file)
                    scale = 'scale=320:-2' if size[0] < size[1] else 'scale=-2:320'
                    external.call('ffmpeg', '-y', '-i', source_file, 
                                  '-map_metadata', '0', '-vf', scale,
                                  '{}_{}'.format(destination, extension))

                logging.debug("Moved from %s to %s%s", source_file, destination, extension)
                os.rename(source_file, '{}{}'.format(destination, extension))

        except Exception as ex:
            logging.warning(ex)

    def _get_new_name_from_exif(self, source_file):
        try:
            image = Image.open(source_file)
            date_string = image._getexif()[36867].replace(
                ':', ''
                ).replace(':', '')
            shot_date = dateutil.parser.parse(date_string)
            return shot_date.strftime(self.file_format)
        except Exception as ex:
            logging.warning('%s: %s', source_file, ex)
            return None

    def _get_new_name_from_video_metadata(self, source_file):
        try:
            video_info = VideoInfo(source_file)
            shot_string = video_info.video('recorded_date')
            if not shot_string:
                shot_string = video_info.video('encoded_date')
            if not shot_string:
                logging.warning('%s: %s', source_file, "failed to get date")
                return None
            shot_date = dateutil.parser.parse(shot_string.replace('UTC ', ''))
            shot_date = shot_date.replace(tzinfo=dateutil.tz.tzutc())
            shot_date = shot_date.astimezone(dateutil.tz.tzlocal())
            return shot_date.strftime(self.file_format)
        except Exception as ex:
            logging.warning('%s: %s', source_file, ex)
            return None

def _get_video_size(source_file):
    video_info = VideoInfo(source_file)
    size = (video_info.video('width'), video_info.video('height'))
    rotation = video_info.video('rotation')
    if rotation and (rotation == 90 or rotation == 270):
        size = (size[1], size[0])
    return size


if __name__ == "__main__":
    IMAGE_MOVER = ImageMover()
    IMAGE_MOVER.start()
