import sys
sys.path.append(".")
import numpy as np
import argparse
import os
import SimpleITK as sitk
from tqdm import tqdm
import nibabel as nib
from config import config
from utils import combine_labels_predicting, dim_recovery
from dataset import test_time_crop


def init_args():

    parser = argparse.ArgumentParser()
    parser.add_argument('-m', '--method', type=int, help='0 stands for majority_voting; 1 stands for averaging')

    return parser.parse_args()


def majority_voting_for_one_patient_with_prob_comparison(patient_name, models):

    preds_cur_patient = []
    # probs_cur_patient = np.empty([len(models), 144, 192, 160])
    probs_cur_patient = []
    for model in models:
        result_path = os.path.join("../pred", model, patient_name)
        sitkImage = sitk.ReadImage(result_path)
        pred_cur_patient = sitk.GetArrayFromImage(sitkImage)
        preds_cur_patient.append(pred_cur_patient)
        prob_path = os.path.join("../pred", model + '_probabilityMap', patient_name.split(".")[0] + ".npy")
        # read predicted prop
        prob_cur_patient = np.load(prob_path)
        assert prob_cur_patient.shape == (3, 144, 192, 160), "The shape of probability map must be (3, 144, 192, 160)"
        probs_cur_patient.append(prob_cur_patient)  # [num_models * (3, 144, 192, 160)]
    preds_cur_patient = np.array(preds_cur_patient, dtype=np.int8)
    preds_cur_patient = test_time_crop(preds_cur_patient)  # [num_models * (3, 144, 192, 160)]
    probs_cur_patient = np.array(probs_cur_patient, dtype=np.float)
    # determine voxels with discrimination to reduce computation
    mask_same = (preds_cur_patient[0] == preds_cur_patient[1])
    for i in range(2, preds_cur_patient.shape[0]):
        mask_same = mask_same * (preds_cur_patient[0] == preds_cur_patient[i])
    mask_diff = np.ones(mask_same.shape) - mask_same
    candidates = np.array(np.where(mask_diff))
    voted_preds = preds_cur_patient[0]
    # iterate (i, j, k)
    for idx in range(candidates.shape[-1]):
        coord = candidates[:, idx]
        z, y, x = tuple(coord)
        label_dict = {0: 0, 1: 0, 2: 0, 4: 0}
        preds_array = preds_cur_patient[:, z, y, x]
        for pred_label in preds_array:
            label_dict[pred_label] += 1
        maxNum_vote = max(label_dict.values())
        majority_labels = []
        for pred_label, v in label_dict.items():
            if v == maxNum_vote:
                majority_labels.append(pred_label)
        if 0 in majority_labels:  #[0,1,2]
            if len(majority_labels) == 1:
                voted_preds[z, y, x] = 0
                continue
            else:
                majority_labels.remove(0)
        if len(majority_labels) > 1:
            probs_array = probs_cur_patient[:, :, z, y, x]
            avg_props = []
            for label in majority_labels:
                if label == 1:
                    label_idx = 0
                elif label == 2:
                    label_idx = 1
                else:
                    label_idx = 2
                prob_tobe_compared = probs_array[preds_array == label][:, label_idx]
                avg_props.append(prob_tobe_compared.mean())
            voted_preds[z, y, x] = majority_labels[np.argmax(avg_props)]
        else:
            voted_preds[z, y, x] = majority_labels[0]

    return dim_recovery(voted_preds)


def ensemble_majority_voting(models):

    # get patients name
    pred_path = os.path.join("../pred", models[0])
    patients_name = os.listdir(pred_path)
    # patients_name = ['BraTS20_Validation_105.nii.gz']

    ensemble_process = tqdm(patients_name)
    for (i, patient) in enumerate(ensemble_process):

        ensemble_process.set_description("Processing Patient:%d" % (i))
        output_array = majority_voting_for_one_patient_with_prob_comparison(patient, models)
        patient_name = patient.split(".")[0]
        affine = nib.load(os.path.join(config["test_path"], patient_name, patient_name + '_t1.nii.gz')).affine
        output_array = output_array.swapaxes(-3, -1)  # convert channel first (SimpleTIK) to channel last (Nibabel)
        output_image = nib.Nifti1Image(output_array, affine)
        if not os.path.exists(config["prediction_dir"]):
            os.mkdir(config["prediction_dir"])
        output_image.to_filename(os.path.join(config["prediction_dir"], patient))


def averaging_for_one_patient(patient_name, models, threshold=0.5):
    probs_cur_patient = []
    for model in models:
        prob_path = os.path.join("../pred", model + "_probabilityMap", patient_name + ".npy")
        # read predicted prop
        prob_cur_patient = np.load(prob_path)
        probs_cur_patient.append(prob_cur_patient)
    probs_cur_patient = np.array(probs_cur_patient, dtype=np.float)
    probs_cur_patient = dim_recovery(probs_cur_patient)
    probs_cur_patient_averaged = np.mean(probs_cur_patient, 0)
    return np.array(probs_cur_patient_averaged > threshold, dtype=float)  # (3, 155, 240, 240)


def ensemble_averaging(models):
    # get patients name
    pred_path = os.path.join("../pred", models[0])
    patients_name = os.listdir(pred_path)

    ensemble_process = tqdm(patients_name)
    for (i, patient) in enumerate(ensemble_process):

        ensemble_process.set_description("Processing Patient:%d" % (i))
        patient_name = patient.split(".")[0]
        output_array = averaging_for_one_patient(patient_name, models)
        output_array = combine_labels_predicting(output_array)
        print("WTs: {:d}".format((output_array == 1).sum()))
        print("TCs: {:d}".format((output_array == 2).sum()))
        print("ETs: {:d}".format((output_array == 4).sum()))
        affine = nib.load(os.path.join(config["test_path"], patient_name, patient_name + '_t1.nii.gz')).affine
        output_array = output_array.swapaxes(-3, -1)  # convert channel first (SimpleTIK) to channel last (Nibabel)
        output_image = nib.Nifti1Image(output_array, affine)
        if not os.path.exists(config["prediction_dir"]):
            os.mkdir(config["prediction_dir"])
        output_image.to_filename(os.path.join(config["prediction_dir"], patient))


if __name__ == "__main__":

    args = init_args()
    method = args.method

    # model_list = ["nml4_lr_loss_crop_275_0.1626_0.9142"]
    # model_list.append("nml4_lr_loss_crop_137_0.1665_0.9125")
    # model_list.append("nml4_lr_loss_crop_217_0.1648_0.9139")
    # model_list.append("nml4_lr_loss_115_0.1639_0.9135")
    # model_list.append("nml4_lr_loss_121_0.1703_0.9141")
    # model_list.append("nml4_lr_loss_155_0.1634_0.9155")
    # model_list.append("nml4_lr_loss_crop_276_trloss_0.1460_0.9326_0.8705")
    # model_list.append("nml4_lr_loss_crop_349_trloss_0.1358_0.9403_0.8822")
    # model_list.append("nml4_lr_loss_crop_394_trloss_0.1325_0.9422_0.8854")

    model_list = ['nml4_lr_loss_crop_[214]_254_0.1633_0.9120_0.8496']
    model_list.append("nml4_lr_loss_crop_[214]_261_0.1624_0.9100_0.8506")
    model_list.append("nml4_lr_loss_crop_[214]_294_0.1639_0.9113_0.8486")
    model_list.append("nml4_lr_loss_crop_[214]_v2_165_0.1616_0.9135_0.8516")
    model_list.append("nml4_lr_loss_crop_[214]_v2_184_0.1636_0.9136_0.8495")
    model_list.append("nml4_lr_loss_crop_[214]_v2_205_0.1643_0.9118_0.8483")
    model_list.append("nml4_lr_loss_crop_[214]_v2_244_0.1665_0.9112_0.8509")
    model_list.append("nml4_lr_loss_crop_[214]_v2_271_0.1667_0.9111_0.8507")
    model_list.append("nml4_lr_loss_crop_[214]_v2_289_0.1666_0.9112_0.8508")

    config["test_path"] = os.path.join(config["base_path"], "data", "MICCAI_BraTS2020_ValidationData")

    if method == 0:
        config["prediction_dir"] = os.path.join("../", "pred", "ensemble_{}_[214]_voting".format(len(model_list)))
        ensemble_majority_voting(model_list)

    elif method == 1:
        config["prediction_dir"] = os.path.join("../", "pred", "ensemble_{}_[214]averaging".format(len(model_list)))
        ensemble_averaging(model_list)
