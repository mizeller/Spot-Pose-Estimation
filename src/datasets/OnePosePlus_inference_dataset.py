from typing import List
from loguru import logger
import os.path as osp
import torch
import numpy as np
from torch.utils.data import Dataset

from src.utils.data_io import read_grayscale
from src.utils import data_utils
from src.utils import vis_utils


class OnePosePlusInferenceDataset(Dataset):
    def __init__(
        self,
        sfm_dir,
        image_paths,
        shape3d,
        img_pad=False,
        img_resize=512,
        df=8,
        pad=True,
        n_images=None,  # Used for debug
        DBG: bool = False,
        # keep this attributes for legacy/compatibiliby reasons with demo.py
        load_pose_gt: bool = True,
        coarse_scale: float = 1 / 8,
        demo_mode: bool = False,
        load_3d_coarse: bool = True,
        preload: bool = False,
    ) -> None:
        super().__init__()
        self.DBG: bool = DBG
        self.shape3d: int = shape3d
        self.pad: bool = pad
        self.image_paths: List[str] = (
            image_paths[:: int(len(image_paths) / n_images)]
            if n_images is not None
            else image_paths
        )
        logger.info(f"Will process:{len(self.image_paths)} images. ")
        self.img_pad = img_pad
        self.img_resize = img_resize
        self.df = df

        # Load pointcloud and point feature
        avg_anno_3d_path = self.get_default_paths(sfm_dir)
        (
            self.keypoints3d,
            self.avg_descriptors3d,
            self.avg_coarse_descriptors3d,
            self.avg_scores3d,
            self.num_3d_orig,
        ) = self.read_anno3d(avg_anno_3d_path, pad=self.pad)

    def get_default_paths(self, sfm_model_dir):
        anno_dir = osp.join(sfm_model_dir, f"anno")
        avg_anno_3d_path = osp.join(anno_dir, "anno_3d_average.npz")

        return avg_anno_3d_path

    def get_intrin_by_color_pth(self, img_path):
        image_dir_name = osp.basename(osp.dirname(img_path))
        object_2D_detector = "GT"
        if "_" in image_dir_name and "_full" not in image_dir_name:
            object_2D_detector = image_dir_name.split("_", 1)[1]

        if object_2D_detector == "GT":
            intrin_name = "intrin_ba"
        elif object_2D_detector == "SPP+SPG":
            intrin_name = "intrin_SPP+SPG"
        elif object_2D_detector == "loftr":
            intrin_name = "intrin_loftr"
        else:
            raise NotImplementedError

        img_ext = osp.splitext(img_path)[1]
        intrin_path = img_path.replace(
            "/" + image_dir_name + "/", "/" + intrin_name + "/"
        ).replace(img_ext, ".txt")
        assert osp.exists(intrin_path), f"{intrin_path}"
        K_crop = torch.from_numpy(np.loadtxt(intrin_path))  # [3*3]
        return K_crop

    def get_intrin_original_by_color_pth(self, img_path):
        image_dir_name = osp.basename(osp.dirname(img_path))

        img_ext = osp.splitext(img_path)[1]
        try:
            intrin_path = img_path.replace(
                "/" + image_dir_name + "/", "/intrin/"
            ).replace(img_ext, ".txt")
            assert osp.exists(intrin_path), f"{intrin_path}"
        except:
            intrin_path = img_path.replace(
                "/" + image_dir_name + "/", "/intrin_ba/"
            ).replace(img_ext, ".txt")
            assert osp.exists(intrin_path), f"{intrin_path}"
        K = torch.from_numpy(np.loadtxt(intrin_path))  # [3*3]
        return K

    def get_gt_pose_by_color_pth(self, img_path):
        image_dir_name = osp.basename(osp.dirname(img_path))
        img_ext = osp.splitext(img_path)[1]
        gt_pose_path = img_path.replace(
            "/" + image_dir_name + "/", "/poses_ba/"
        ).replace(img_ext, ".txt")
        assert osp.exists(gt_pose_path), f"{gt_pose_path}"
        pose_gt = torch.from_numpy(np.loadtxt(gt_pose_path))  # [4*4]
        return pose_gt

    def read_anno3d(self, avg_anno3d_file, pad=True):
        """Read(and pad) 3d info"""
        avg_data = np.load(avg_anno3d_file)

        keypoints3d = torch.Tensor(avg_data["keypoints3d"])  # [m, 3]
        avg_descriptors3d = torch.Tensor(avg_data["descriptors3d"])  # [dim, m]
        avg_scores = torch.Tensor(avg_data["scores3d"])  # [m, 1]
        num_3d_orig = keypoints3d.shape[0]

        avg_anno3d_coarse_file = (
            osp.splitext(avg_anno3d_file)[0]
            + "_coarse"
            + osp.splitext(avg_anno3d_file)[1]
        )
        avg_coarse_data = np.load(avg_anno3d_coarse_file)
        avg_coarse_descriptors3d = torch.Tensor(
            avg_coarse_data["descriptors3d"]
        )  # [dim, m]
        if self.DBG:
            vis_utils.visualize_pointcloud(npz_file=avg_anno3d_file)

        avg_coarse_scores = torch.Tensor(avg_coarse_data["scores3d"])  # [m, 1]

        if pad:
            (
                keypoints3d,
                padding_index,
            ) = data_utils.pad_keypoints3d_random(keypoints3d, self.shape3d)
            (
                avg_descriptors3d,
                avg_scores,
            ) = data_utils.pad_features3d_random(
                avg_descriptors3d, avg_scores, self.shape3d, padding_index
            )

            (
                avg_coarse_descriptors3d,
                avg_coarse_scores,
            ) = data_utils.pad_features3d_random(
                avg_coarse_descriptors3d,
                avg_coarse_scores,
                self.shape3d,
                padding_index,
            )

        # Preload 3D features to cuda:
        (
            keypoints3d,
            avg_descriptors3d,
            avg_coarse_descriptors3d,
        ) = map(
            lambda x: x.cuda(),
            [
                keypoints3d,
                avg_descriptors3d,
                avg_coarse_descriptors3d,
            ],
        )

        return (
            keypoints3d,
            avg_descriptors3d,
            avg_coarse_descriptors3d,
            avg_scores,
            num_3d_orig,
        )

    def __len__(self):
        return len(self.image_paths)

    def __getitem__(self, index):
        image_path = self.image_paths[index]
        query_img, query_img_scale, query_img_mask = read_grayscale(
            image_path,
            resize=self.img_resize,
            pad_to=self.img_resize if self.img_pad else None,
            ret_scales=True,
            ret_pad_mask=True,
            df=self.df,
        )
        self.h_origin = query_img.shape[1] * query_img_scale[0]
        self.w_origin = query_img.shape[2] * query_img_scale[1]
        self.query_img_scale = query_img_scale

        image_dir_name = osp.basename(osp.dirname(image_path))
        data = {}

        if self.avg_coarse_descriptors3d is not None:
            data.update(
                {
                    "descriptors3d_coarse_db": self.avg_coarse_descriptors3d[
                        None
                    ],  # [1, dim, n2]
                }
            )

        data.update(
            {
                "keypoints3d": self.keypoints3d[None],  # [1, n2, 3]
                "descriptors3d_db": self.avg_descriptors3d[None],  # [1, dim, n2]
                "query_image": query_img[None],  # [1*h*w]
                "query_image_path": image_path,
            }
        )

        return data
