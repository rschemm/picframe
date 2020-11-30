import yaml
import os
import time
import logging
import random
from picframe import exif2dict

DEFAULT_CONFIGFILE = "./picframe/configuration.yaml"
DEFAULT_CONFIG = {
    'viewer': {
        'blur_amount': 12,
        'blur_zoom': 1.0,  
        'blur_edges': False, 
        'edge_alpha': 0.5, 
        'fps': 20.0, 
        'background': [0.2, 0.2, 0.3, 1.0],  
        'blend_type': 0.0, # {"blend":0.0, "burn":1.0, "bump":2.0}
        'font_file': '/home/pi/pi3d_demos/fonts/NotoSans-Regular.ttf', 
        'shader': '/home/pi/pi3d_demos/shaders/blend_new', 
        'show_names_tm': 0.0, 
        'fit': False, 
        'auto_resize': True,
        'kenburns': False,
        'test_key': 'test_value'
    }, 
    'model': {
        'pic_dir': '/home/pi/Pictures', 
        'no_files_img': 'PictureFrame2020img.jpg',
        'subdirectory': '', 
        'check_dir_tm': 60.0, 
        'recent_n': 3, 
        'reshuffle_num': 1, 
        'time_delay': 200.0, 
        'fade_time': 10.0, 
        'shuffle': True,
        'image_attr': ['PICFRAME GPS']                           # image attributes send by MQTT, Keys are taken from exifread library, 'PICFRAME GPS' is special to retrieve GPS lon/lat
    },
    'mqtt': {
        'server': '', 
        'port': 8883, 
        'login': '', 
        'passwort': '', 
        'tls': ''
    }
}
EXTENSIONS = ['.png','.jpg','.jpeg'] # can add to these

class Model:

    def __init__(self, configfile = DEFAULT_CONFIGFILE):
        self.__logger = logging.getLogger("model.Model")
        self.__logger.debug('creating an instance of Model')
        self.__config = DEFAULT_CONFIG
        self.__last_file_change = 0.0
        self.__date_to = None
        self.__date_from = None
        with open(configfile, 'r') as stream:
            try:
                conf = yaml.safe_load(stream)
                for section in ['viewer', 'model', 'mqtt']:
                    self.__config[section] = {**DEFAULT_CONFIG[section], **conf[section]}
                self.__logger.debug('config data = %s', self.__config)
            except yaml.YAMLError as exc:
                print(exc)
        self.__file_list = []
        self.__number_of_files = 0
        self.__get_files()
        self.__file_index = 0
        self.__num_run_through = 0
        
    def get_viewer_config(self):
        return self.__config['viewer']

    def get_model_config(self):
        return self.__config['model']
    
    def get_mqtt_config(self):
        return self.__config['mqtt']
    
    @property
    def fade_time(self):
        return self.__config['model']['fade_time']
    
    @fade_time.setter
    def fade_time(self, time):
        self.__config['model']['fade_time'] = time

    @property
    def time_delay(self):
        return self.__config['model']['time_delay']
    
    @time_delay.setter
    def time_delay(self, time):
        self.__config['model']['time_delay'] = time
    
    @property
    def subdirectory(self):
        return self.__config['model']['subdirectory']
    
    @subdirectory.setter
    def subdirectory(self, dir):
        self.__config['model']['subdirectory'] = dir
        self.__get_files()
    
    @property
    def shuffle(self):
        return self.__config['model']['shuffle']
    
    @shuffle.setter
    def shuffle(self, val:bool):
        self.__config['model']['shuffle'] = val
        if val == True:
            self.__shuffle_files()
        else:
            self.__sort_files()
    
    def check_for_file_changes(self):
    # check modification time of pic_dir and its sub folders
        update = False
        pic_dir = self.get_model_config()['pic_dir']
        for root, _, _ in os.walk(pic_dir):
            mod_tm = os.stat(root).st_mtime
            if mod_tm > self.__last_file_change:
                self.__last_file_change = mod_tm
                self.__logger.info('files changed in %s at %s', pic_dir, self.__last_file_change)
                update = True
        if update == True:
            self.__get_files()
            self.__num_run_through = 0
            self.__file_index = 0
        return update
    
    def __get_image_date(self, file_path_name):
        dt = os.path.getmtime(file_path_name) # use file last modified date as default
        try:
            exifs = exif2dict.Exif2Dict(file_path_name)
            val = exifs.get_exif('EXIF DateTimeOriginal')
            if val['EXIF DateTimeOriginal'] != None:
                dt = time.mktime(time.strptime(val['EXIF DateTimeOriginal'], '%Y:%m:%d %H:%M:%S'))
        except OSError as e:
            self.__logger.warning("Can't extract exif data from file: \"%s\"", file_path_name)
            self.__logger.warning("Cause: %s", e.args[1])
        return dt

    def __get_image_attr(self, file_path_name):
        orientation = 1
        image_attr_list = {}
        try:
            exifs = exif2dict.Exif2Dict(file_path_name)
            val = exifs.get_exif('EXIF Orientation')
            if val['EXIF Orientation'] != None:
                orientation = int(val['EXIF Orientation'])
            for exif in self.get_model_config()['image_attr']:
                if (exif == 'PICFRAME GPS'):
                    image_attr_list.update(exifs.get_locaction())
                else:
                    image_attr_list.update(exifs.get_exif(exif))
        except OSError as e:
            self.__logger.warning("Can't extract exif data from file: \"%s\"", file_path_name)
            self.__logger.warning("Cause: %s", e.args[1])
        return orientation, image_attr_list

    def __get_files(self):
        self.__file_list = []
        picture_dir = os.path.join(self.get_model_config()['pic_dir'], self.get_model_config()['subdirectory'])
        for root, _dirnames, filenames in os.walk(picture_dir):
            mod_tm = os.stat(root).st_mtime # time of alteration in a directory
            if mod_tm > self.__last_file_change:
                self.__last_file_change = mod_tm
            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                if ext in EXTENSIONS and not '.AppleDouble' in root and not filename.startswith('.'):
                    file_path_name = os.path.join(root, filename)
                    dt = self.__get_image_date(file_path_name)
                    # iFiles now list of lists [file_name, exif or if not exist file_changed_date] 
                    self.__file_list.append([file_path_name, dt])
            if len(self.__file_list) == 0:
                img = self.get_model_config()['no_files_img']
                dt = self.__get_image_date(img)
                self.__file_list.append([img, dt])
        if self.get_model_config()['shuffle']:
            self.__shuffle_files()
        else:
            self.__sort_files()
        self.__number_of_files = len(self.__file_list)
    
    def get_next_file(self, date_from:tuple = None, date_to:tuple = None):
        if self.__file_index == self.__number_of_files:
            self.__num_run_through += 1
            if self.get_model_config()['shuffle'] and (self.__num_run_through >= self.get_model_config()['reshuffle_num']):
                self.__num_run_through = 0
                self.__shuffle_files()
            self.__file_index = 0

        found = False
        for i in range(0,self.get_number_of_files()):
            if date_from is not None:
                if self.__file_list[self.__file_index][1] < time.mktime(date_from + (0, 0, 0, 0, 0, 0)):
                    self.__file_index = (self.__file_index + 1) % self.get_number_of_files()
                    continue
            if date_to is not None:
                if self.__file_list[self.__file_index][1] > time.mktime(date_to + (0, 0, 0, 0, 0, 0)):
                    self.__file_index = (self.__file_index + 1) % self.get_number_of_files()
                    continue
            found = True
            break
        if found == True:
            file = self.__file_list[self.__file_index][0]
        else:
            file = self.get_model_config()['no_files_img']
        orientation, image_attr = self.__get_image_attr(file)
        self.__file_index  += 1
        self.__logger.info('Next file in list: %s', file)
        self.__logger.debug('Image attributes: %s', image_attr)
        return file, orientation, image_attr
        

    def __shuffle_files(self):
        self.__file_list.sort(key=lambda x: x[1]) # will be later files last
        recent_n = self.get_model_config()['recent_n']
        temp_list_first = self.__file_list[-recent_n:]
        temp_list_last = self.__file_list[:-recent_n]
        random.shuffle(temp_list_first)
        random.shuffle(temp_list_last)
        self.__file_list = temp_list_first + temp_list_last
    
    def __sort_files(self):
        self.__file_list.sort() # if not shuffled; sort by name

    def get_number_of_files(self):
        return self.__number_of_files
    

    
    