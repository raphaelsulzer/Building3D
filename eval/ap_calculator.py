import os

import numpy as np
from scipy.spatial.distance import cdist
from scipy.optimize import linear_sum_assignment


def hausdorff_distance_line(p_line, t_line, sample_points=20):
    r"""
    :param p_line: (N, 2, 3), here N is number queries
    :param t_line: (M, 2, 3), here M is the number of gt lines
    :param sample_points: int, sample sample_points from start points to end points and include them
    :return: N x M matrix
    """
    N, M = p_line.shape[0], t_line.shape[0]
    if N == 0:
        return np.array([])

    # Sample Points
    all_lines = np.concatenate((p_line, t_line), axis=0)
    weights = np.linspace(0, 1, sample_points).reshape(1, sample_points, 1)
    # N+M, sample_points, 3
    all_points = all_lines[:, 0, :][:, np.newaxis, :] + weights * (
            all_lines[:, 1, :][:, np.newaxis, :] - all_lines[:, 0, :][:, np.newaxis, :])

    # Calculate Hausdorff distance
    distance_matrix = cdist(all_points[:N, :, :].reshape(-1, 3), all_points[N:N + M, :, :].reshape(-1, 3),
                            'euclidean')  # p=2 means Euclidean Distance
    distance_matrix = distance_matrix.reshape(N, sample_points, M, sample_points)
    distance_matrix = np.transpose(distance_matrix, axes=(0, 2, 1, 3))
    h_pt_value = distance_matrix.min(-1).max(-1, keepdims=True)  # h(Prediction, Target)
    h_tp_value = distance_matrix.min(-2).max(-1, keepdims=True)  # h(Target, Prediction)
    hausdorff_matrix = np.concatenate((h_pt_value, h_tp_value), axis=-1)
    hausdorff_matrix = hausdorff_matrix.max(-1)

    return hausdorff_matrix

def graph_edit_distance(pd_vertices, pd_edges, gt_vertices, gt_edges, wed_v):
    wed_e = 0
    if len(pd_vertices) > 0:
        distances = cdist(pd_vertices, gt_vertices)
        wed_v += sum(np.min(distances, axis=1))
        min_indices = np.argmin(distances, axis=1)
        for i, index in enumerate(min_indices):
            pd_vertices[i] = gt_vertices[index]
        unique_pd_vertices = np.unique(pd_vertices, axis=0)
        renew_pd_edges = pd_edges.copy()
        for i, point in enumerate(unique_pd_vertices):
            v_indexs = np.where((pd_vertices == point).all(axis=1))[0]
            for v_index in v_indexs:
                renew_pd_edges[pd_edges == v_index] = i
        renew_pd_edges = np.unique(renew_pd_edges, axis=0)

        gt_edges_copy = gt_edges.copy()
        for edge in renew_pd_edges:
            e1_index = np.where((gt_vertices == unique_pd_vertices[edge[0]]).all(axis=1))[0]
            e2_index = np.where((gt_vertices == unique_pd_vertices[edge[1]]).all(axis=1))[0]
            a = np.where((gt_edges == np.array(sorted([e1_index[0], e2_index[0]]))).all(axis=1))[0]
            if len(a):
                mask = np.any(gt_edges_copy != np.array(sorted([e1_index[0], e2_index[0]])), axis=1)
                gt_edges_copy = gt_edges_copy[mask]
            else:
                wed_e += np.linalg.norm(unique_pd_vertices[edge[0]] - unique_pd_vertices[edge[1]])
    else:
        gt_edges_copy = gt_edges.copy()
        wed_v = 0

    for edge in gt_edges_copy:
        wed_e += np.linalg.norm(gt_vertices[edge[0]] - gt_vertices[edge[1]])

    sum_distance = 0
    for edge in gt_edges:
        sum_distance += np.linalg.norm(gt_vertices[edge[0]] - gt_vertices[edge[1]])

    wde = (wed_e + wed_v) / sum_distance
    return wde

def computer_edges(edges, vertices):
    # return edge index
    index = []
    for edge in edges:
        indices = []
        for point in edge:
            matching_indices = np.where((vertices == point).all(axis=1))[0]
            if len(matching_indices) > 0:
                indices.append(matching_indices[0])
            else:
                indices.append(-1)

        index.append(indices)

    return np.sort(np.array(index), axis=-1)

class APCalculator(object):
    def __init__(self, distance_thresh=4, confidence_thresh=0.7):
        r"""
        :param distance_thresh: the distance thresh
        :param confidence_thresh: the edges confident thresh
        """
        self.distance_thresh = distance_thresh
        self.confidence_thresh = confidence_thresh
        self.ap_dict = {'tp_corners': 0, 'num_pred_corners': 0, 'num_label_corners': 0, 'distance': 0, 'tp_edges': 0,
                        'num_pred_edges': 0, 'num_label_edges': 0, 'average_corner_offset': 0, 'corners_precision': 0,
                        'corners_recall': 0, 'corner_f1': 0, 'edges_precision': 0, 'edges_recall': 0, 'edges_f1': 0}

    def compute_metrics(self, batch):
        r"""
        :param batch: batch_size, predicted_corners, predicted_edges, predicted_score, wf_vertices, wf_edges, centroid,
        max_distance
        : test case
            batch_size = np.array([1])
            predicted_vertices = np.array([[1, 2, 3], [7, 8, 9], [4, 5, 1], [7, 8, 9], [5,3,2], [1, 2, 4], [2, 5, 7]])
            label_corners = np.array([[2, 3, 4], [5, 6, 4], [6, 7, 8]])
            predicted_edges = np.array([[1, 2], [1, 5], [2, 4]])
            label_edges = np.array([[0, 1], [0, 2], [1, 2], [3, 4], [4, 5],[2, 4], [1, 3], [-1, -1], [-1, -1]])
            centroid = np.array([1, 2, 3])
            max_distance = np.array([[2]])
            predicted_score = np.array([[0.8, 0.8, 0.3, 1]]
        :return: AP Dict
        """
        batch_size = len(batch['predicted_vertices'])
        predicted_corners, predicted_edges = batch['predicted_vertices'], batch['predicted_edges']
        pred_edges_vertices = batch['pred_edges_vertices']
        label_corners, label_edges = batch['wf_vertices'], batch['wf_edges']
        label_edges_vertices = batch['wf_edges_vertices']
        # centroid, max_distance = batch['centroid'], batch['max_distance']

        for b in range(batch_size):
            # ----------------------- Confidence Thresh ---------------------------
            p_edges = predicted_edges[b]
            predicted_edges_indices = p_edges.flatten()
            used_predicted_edges_indices = np.unique(predicted_edges_indices)
            if len(used_predicted_edges_indices) != 0:
                new_hausdorff_distance = hausdorff_distance_line(pred_edges_vertices, label_edges_vertices)
                new_predict_indices, new_label_indices = linear_sum_assignment(
                    new_hausdorff_distance)
                edge_mask = new_hausdorff_distance[new_predict_indices, new_label_indices] <= 0.1
                pr_corners = pred_edges_vertices[new_predict_indices[edge_mask]]
                gt_corners = label_edges_vertices[new_label_indices[edge_mask]]

                tp_corners = len(np.unique(pr_corners.reshape(-1, 3), axis=0))
                tp_fp_corners = len(predicted_corners)
                tp_fn_corners = len(label_corners)

                # distances = 0
                pr_vertices = np.unique(pr_corners.reshape(-1, 3), axis=0)
                gt_vertices = np.unique(gt_corners.reshape(-1, 3), axis=0)
                distance_matrix = cdist(pr_vertices, gt_vertices)
                predict_indices, label_indices = linear_sum_assignment(distance_matrix)
                mask = distance_matrix[predict_indices, label_indices] <= 0.1
                predict_indices = predict_indices[mask]
                label_indices = label_indices[mask]
                distances = np.sum(
                    distance_matrix[predict_indices, label_indices][:, np.newaxis])
                tp_edges = sum(edge_mask)
                tp_fp_edges = len(predicted_edges)
                tp_fn_edges = len(label_edges)

                # wireframe edit distance
                for k, indices in enumerate(new_predict_indices[edge_mask]):
                    pred_edges_vertices[indices] = label_edges_vertices[new_label_indices[edge_mask][k]]
                predicted_corners = label_edges_vertices.reshape(-1, 3)
                predicted_corners = np.unique(predicted_corners, axis=0)
                submission_edges = computer_edges(label_edges_vertices, predicted_corners)
                wde = graph_edit_distance(predicted_corners, submission_edges.copy(), label_corners.copy(),
                                          label_edges.copy(), distances)

            else:
                tp_corners = 0
                tp_fp_corners = 0
                tp_fn_corners = len(label_corners)
                tp_edges = 0
                tp_fp_edges = 0
                tp_fn_edges = len(label_edges)
                distances = 0
                wde = 1

            # ------------------------------- Return AP Dict ------------------------------
            self.ap_dict['tp_corners'] += tp_corners
            self.ap_dict['num_pred_corners'] += tp_fp_corners
            self.ap_dict['num_label_corners'] += tp_fn_corners

            self.ap_dict['distance'] += distances

            self.ap_dict['tp_edges'] += tp_edges
            self.ap_dict['num_pred_edges'] += tp_fp_edges
            self.ap_dict['num_label_edges'] += tp_fn_edges

    def output_accuracy(self):
        self.ap_dict['average_corner_offset'] = self.ap_dict['distance'] / self.ap_dict['tp_corners']
        self.ap_dict['corners_precision'] = self.ap_dict['tp_corners'] / self.ap_dict['num_pred_corners']
        self.ap_dict['corners_recall'] = self.ap_dict['tp_corners'] / self.ap_dict['num_label_corners']
        self.ap_dict['corners_f1'] = 2 * self.ap_dict['corners_precision'] * self.ap_dict['corners_recall'] / (
                self.ap_dict['corners_precision'] + self.ap_dict['corners_recall'])

        self.ap_dict['edges_precision'] = self.ap_dict['tp_edges'] / self.ap_dict['num_pred_edges']
        self.ap_dict['edges_recall'] = self.ap_dict['tp_edges'] / self.ap_dict['num_label_edges']
        self.ap_dict['edges_f1'] = 2 * self.ap_dict['edges_precision'] * self.ap_dict['edges_recall'] / (
                    self.ap_dict['edges_precision'] + self.ap_dict['edges_recall'])

        print('Average Corner offset', self.ap_dict['average_corner_offset'])
        print('Corners Precision: ', self.ap_dict['corners_precision'])
        print('Corners Recall: ', self.ap_dict['corners_recall'])
        print('Corners F1：', self.ap_dict['corners_f1'])

        print('Edges Precision: ', self.ap_dict['edges_precision'])
        print('Edges Recall: ', self.ap_dict['edges_recall'])
        print('Edges F1: ', self.ap_dict['edges_f1'])

    def reset(self):
        self.ap_dict = {'tp_corners': 0, 'num_pred_corners': 0, 'num_label_corners': 0, 'distance': 0, 'tp_edges': 0,
                        'num_pred_edges': 0, 'num_label_edges': 0, 'average_corner_offset': 0, 'corners_precision': 0,
                        'corners_recall': 0, 'corners_f1': 0, 'edges_precision': 0, 'edges_recall': 0, 'edges_f1': 0}

