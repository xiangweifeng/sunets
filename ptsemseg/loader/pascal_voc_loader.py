import os
import random
import collections
import torch
import numpy as np
import scipy.misc as m
import scipy.io as io

import matplotlib.pyplot as plt
from PIL import Image, ImageMath

from tqdm import tqdm
from torch.utils import data
from torchvision.transforms import Compose, Normalize, ToTensor, Resize

from .. import get_data_path

class pascalVOCLoader(data.Dataset):
    def __init__(self, root, split="train_aug", is_transform=False, img_size=512):
        self.root = root
        self.split = split
        self.is_transform = is_transform
        self.ignore_index = 255
        self.n_classes = 21
        self.img_size = img_size if isinstance(img_size, tuple) else (img_size, img_size)
        self.files = collections.defaultdict(list)

        self.image_transform = Compose([
            ToTensor(),
            Normalize([.485, .456, .406], [.229, .224, .225]),
        ])
        self.filler = [0, 0, 0]

        # Reading pascal VOC dataset list
        self.voc_path = get_data_path('pascal')
        for split in ["train", "val", "trainval", "test"]:
            file_list = tuple(open(self.voc_path + '/ImageSets/Segmentation/' + split + '.txt', 'r'))
            file_list = [id_.rstrip() for id_ in file_list]
            self.files[split] = file_list

        # Reading SBD dataset list
        self.sbd_path = get_data_path('sbd')
        self.sbd_train_list = tuple(open(self.sbd_path + 'dataset/train_withValdata.txt', 'r'))
        self.sbd_train_list = [id_.rstrip() for id_ in self.sbd_train_list]

        self.sbd_val_list = tuple(open(self.sbd_path + 'dataset/val.txt', 'r'))
        self.sbd_val_list = [id_.rstrip() for id_ in self.sbd_val_list]

        # Augmenting pascal and SBD dataset list
        self.files['trainval_aug'] = self.sbd_train_list+self.sbd_val_list+self.files['train']
        self.files['train_aug'] = list(set(self.files['trainval_aug']) - set(self.files['val']))

        # needed for extracting GT of sbd and pascal dataset
        if not os.path.isdir(self.root + '/combined_annotations'):
            self.setup(pre_encode=True)
        else:
            self.setup(pre_encode=False)

        self.files = self.files[self.split]

    def __len__(self):
        return len(self.files)

    def __getitem__(self, index):

        img_name = self.files[index]
        img, lbl = self.readfile(img_name)

        if self.split is 'val':
            img, lbl = self.r_crop(img, lbl)
        elif self.is_transform:
            img, lbl = self.transform(img, lbl)

        # mean subtraction
        img = self.image_transform(img)

        lbl = np.array(lbl, dtype=np.int32)
        lbl = torch.from_numpy(lbl).long()

        return img, lbl

    def readfile(self, img_name):

        img_path = self.voc_path + 'JPEGImages/' + img_name + '.jpg'
        lbl_path = self.root + '/combined_annotations/' + img_name + '.png'

        img = Image.open(img_path).convert('RGB')
        lbl = Image.open(lbl_path).convert('P')

        return img, lbl

    def transform(self, img, lbl):
        # Scaling
        img, lbl = self.r_scale(img, lbl)

        # Cropping
        img, lbl = self.r_crop(img, lbl)

        # Flipping
        img, lbl = self.r_flip(img, lbl)

        # Rotation
        img, lbl = self.r_rotate(img, lbl)

        return img, lbl

    def r_scale(self, img, lbl, low=0.5, high=2.0):
        w, h = img.size

        resize = random.uniform(low, high)

        new_w, new_h = int(resize * w), int(resize * h)

        image_transform = Resize(size=(new_h, new_w))
        label_transform = Resize(size=(new_h, new_w), interpolation=Image.NEAREST)

        return (image_transform(img), label_transform(lbl))

    def r_crop(self, img, lbl):
        w, h = img.size
        th, tw = self.img_size
        if w < tw or h < th:
            padw, padh = max(tw - w, 0), max(th - h, 0)
            w += padw
            h += padh
            im = Image.new(img.mode, (w, h), tuple(self.filler))
            im.paste(img, (int(padw/2),int(padh/2)))
            l = Image.new(lbl.mode, (w, h), self.ignore_index)
            l.paste(lbl, (int(padw/2),int(padh/2)))
            img = im
            lbl = l
        if w == tw and h == th:
            return img, lbl
        x1 = random.randint(0, w - tw)
        y1 = random.randint(0, h - th)
        return (img.crop((x1,y1, x1 + tw, y1 + th)),  lbl.crop((x1,y1, x1 + tw, y1 + th)))

    def r_flip(self, img, lbl):
        if random.random() < 0.5:
            return img.transpose(Image.FLIP_LEFT_RIGHT), lbl.transpose(Image.FLIP_LEFT_RIGHT)
        return img, lbl

    def r_rotate(self, img, lbl):
        angle = random.uniform(-10, 10)

        lbl = np.array(lbl, dtype=np.int32) - self.ignore_index
        lbl = Image.fromarray(lbl)
        img = tuple([ImageMath.eval("int(a)-b", a=j, b=self.filler[i]) for i, j in enumerate(img.split())])

        lbl = lbl.rotate(angle, resample=Image.NEAREST)
        img = tuple([k.rotate(angle, resample=Image.BICUBIC) for k in img])

        lbl = ImageMath.eval("int(a)+b", a=lbl, b=self.ignore_index)
        img = Image.merge(mode='RGB', bands=tuple(
            [ImageMath.eval("convert(int(a)+b,'L')", a=j, b=self.filler[i]) for i, j in enumerate(img)]))
        return (img, lbl)

    def get_pascal_labels(self):
        return np.asarray([[0,0,0], [128,0,0], [0,128,0], [128,128,0], [0,0,128], [128,0,128],
                              [0,128,128], [128,128,128], [64,0,0], [192,0,0], [64,128,0], [192,128,0],
                              [64,0,128], [192,0,128], [64,128,128], [192,128,128], [0, 64,0], [128, 64, 0],
                              [0,192,0], [128,192,0], [0,64,128]])

    def encode_segmap(self, mask):
        mask = mask.astype(int)
        label_mask = np.ones((mask.shape[0], mask.shape[1]), dtype=np.int16)*self.ignore_index
        for i, label in enumerate(self.get_pascal_labels()):
            label_mask[np.all(mask == np.array(label).reshape(1,1,3), axis=2)] = i
        label_mask = label_mask.astype(int)
        return label_mask

    def decode_segmap(self, temp, plot=False):
        label_colours = self.get_pascal_labels()
        r = temp.copy()
        g = temp.copy()
        b = temp.copy()
        for l in range(0, self.n_classes):
            r[temp == l] = label_colours[l, 0]
            g[temp == l] = label_colours[l, 1]
            b[temp == l] = label_colours[l, 2]

        rgb = np.zeros((temp.shape[0], temp.shape[1], 3))
        rgb[:, :, 0] = r
        rgb[:, :, 1] = g
        rgb[:, :, 2] = b
        if plot:
            plt.imshow(rgb)
            plt.show()
        else:
            return rgb

    def setup(self, pre_encode=False):

        target_path = self.root + '/combined_annotations/'
        if not os.path.exists(target_path):
            os.makedirs(target_path)

        if pre_encode:
            print("Pre-encoding segmentation masks...")
            for i in tqdm(self.sbd_train_list):
                lbl_path = self.sbd_path + 'dataset/cls/' + i + '.mat'
                lbl = io.loadmat(lbl_path)['GTcls'][0]['Segmentation'][0].astype(np.int32)
                lbl = m.toimage(lbl, high=self.ignore_index, low=0)
                m.imsave(target_path + i + '.png', lbl)
            for i in tqdm(self.sbd_val_list):
                lbl_path = self.sbd_path + 'dataset/cls/' + i + '.mat'
                lbl = io.loadmat(lbl_path)['GTcls'][0]['Segmentation'][0].astype(np.int32)
                lbl = m.toimage(lbl, high=self.ignore_index, low=0)
                m.imsave(target_path + i + '.png', lbl)
            for i in tqdm(self.files['trainval']):
                lbl_path = self.voc_path + 'SegmentationClass/' + i + '.png'
                lbl = self.encode_segmap(m.imread(lbl_path))
                lbl = m.toimage(lbl, high=self.ignore_index, low=0)
                m.imsave(target_path + i + '.png', lbl)
