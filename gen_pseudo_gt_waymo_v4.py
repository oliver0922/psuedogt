import copy
import open3d
import open3d as o3d
import numpy as np
import pandas as pd
import argparse
import os
from scipy.spatial.transform import Rotation as R
from utils.utils import dbscan, get_obj,translate_boxes_to_open3d_instance, translate_boxes_to_open3d_gtbox, dbscan_max_cluster, translate_boxes_to_lidar_coords
from utils.registration_utils import full_registration
from utils.open3d_utils import set_black_background, set_white_background

AXIS_PCD = open3d.geometry.TriangleMesh.create_coordinate_frame(size=2.0, origin=[0, 0, 0])
CAR_CLASS_SIZE = [4.5, 1.9, 2.0] #l, w, h



def main(args):
    
    voxel_size = 0.02
    max_correspondence_distance_coarse = voxel_size * 15.
    max_correspondence_distance_fine = max_correspondence_distance_coarse / 2.
        
    source_full_pc = np.fromfile(os.path.join(args.dataset_path,f'scene-{args.scene_idx}','pointcloud',f'{str(args.rgs_start_idx).zfill(6)}.bin'), dtype=np.float32).reshape(-1, 3)

    src_list = list()
    pcd_id_list = list()
    idx_range = range(args.rgs_start_idx, args.rgs_end_idx+1)
    src_gt_bbox  = np.fromfile(os.path.join(args.dataset_path,f'scene-{args.scene_idx}','annotations',f'{str(args.src_frame_idx).zfill(6)}.bin')).reshape(-1, 7) 

    speed_list = dict()
    estimated_position_list = dict()
    previous_position = dict()
    same_id_dict = dict()
    appeared_id = list()

    loaded_pcd_list = list()
    loaded_pcd_color_list = list()
    loaded_pcd_id_list = list()

   
    for frame_idx in idx_range:
        pcd_with_instance_id = np.fromfile(os.path.join(args.dataset_path,f'scene-{args.scene_idx}','visualization/uppc_continuous_sam',f'{str(frame_idx).zfill(6)}.bin'), dtype=np.float32).reshape(-1, 4)
        pcd_color = np.fromfile(os.path.join(args.dataset_path,f'scene-{args.scene_idx}','visualization/uppc_color_continuous_sam',f'{str(frame_idx).zfill(6)}.bin'), dtype=np.float32).reshape(-1, 3)[:, :3]
        pcd = pcd_with_instance_id[:, :3]
        pcd_id = pcd_with_instance_id[:, 3]

        loaded_pcd_list.append(pcd)
        loaded_pcd_color_list.append(pcd_color)
        loaded_pcd_id_list.append(pcd_id)

    for frame_idx in idx_range:
        pcd = loaded_pcd_list[frame_idx - args.rgs_start_idx]
        pcd_color = loaded_pcd_color_list[frame_idx - args.rgs_start_idx]
        pcd_id = loaded_pcd_id_list[frame_idx - args.rgs_start_idx]

        ####################### dbscan before registration ############################
        if args.perform_db_scan_before_registration:
            id_list = np.unique(pcd_id)
            frame_unnoise_idx = list()
            for i in id_list:
                mask = np.where(pcd_id == i)
                dis = np.mean(np.linalg.norm(pcd[mask], axis=1))
                dis = dis * (0.3 * np.pi / 180) * 2.5
                masked_pcd = o3d.geometry.PointCloud()
                masked_pcd.points = open3d.utility.Vector3dVector(pcd[mask])
                masked_pcd.colors = open3d.utility.Vector3dVector(pcd_color[mask])
                if np.mean(np.linalg.norm(masked_pcd.points, axis=1)) > 40.0:
                    continue
                instance_unnoise_idx = dbscan_max_cluster(masked_pcd, eps=dis, min_points=5)
                if len(instance_unnoise_idx) < 70:
                    continue
                    src = open3d.geometry.PointCloud()
                    src.points = open3d.utility.Vector3dVector(pcd[mask][instance_unnoise_idx])
                    src.colors = open3d.utility.Vector3dVector(pcd_color[mask][instance_unnoise_idx])
                    src.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=0.5, max_nn=30))
                    src.orient_normals_towards_camera_location(np.array([0, 0, 0]))
                    print(f"frame{frame_idx}'s instance{i} point_cloud after dbscan is too small")
                    gt_bbox = np.fromfile(os.path.join(args.dataset_path,f'scene-{args.scene_idx}','annotations',f'{str(frame_idx).zfill(6)}.bin')).reshape(-1, 7)
                    gt_line_list = list()
                    for i in range(gt_bbox.shape[0]):
                        line_set_gt, box3d_gt = translate_boxes_to_open3d_gtbox(gt_bbox[i])
                        line_set_gt.paint_uniform_color([0, 0, 1])
                        gt_line_list.append(line_set_gt)

                    o3d.visualization.draw_geometries([src, AXIS_PCD]+gt_line_list, point_show_normal=True)
                    continue
                frame_unnoise_idx.extend(mask[0][instance_unnoise_idx])
                if args.vis:
                    #print(f"frame{frame_idx}'s instance{i} point_cloud before dbscan")
                    un_dbscanned_src = open3d.geometry.PointCloud()
                    un_dbscanned_src.points = open3d.utility.Vector3dVector(pcd[mask])
                    un_dbscanned_src.colors = open3d.utility.Vector3dVector(pcd_color[mask])
                    #o3d.visualization.draw_geometries_with_key_callbacks([un_dbscanned_src],{ord("B"): set_black_background, ord("W"): set_white_background })

                    #print(f"frame{frame_idx}'s instance{i} point_cloud after dbscan")
                    dbscanned_src = open3d.geometry.PointCloud()
                    dbscanned_src.points = open3d.utility.Vector3dVector(pcd[mask][instance_unnoise_idx])
                    dbscanned_src.colors = open3d.utility.Vector3dVector(pcd_color[mask][instance_unnoise_idx])
                    #o3d.visualization.draw_geometries_with_key_callbacks([dbscanned_src],{ord("B"): set_black_background, ord("W"): set_white_background })

            pcd = pcd[frame_unnoise_idx]
            pcd_color = pcd_color[frame_unnoise_idx]
            pcd_id = pcd_id[frame_unnoise_idx]
        ####################### dbscan before registration ############################

        src = open3d.geometry.PointCloud()
        src.points = open3d.utility.Vector3dVector(pcd)
        src.colors = open3d.utility.Vector3dVector(pcd_color)

        ####################### id merge with position estimation ############################
        if args.id_merge_with_speed:
            for i in range(len(pcd_id)):
                while pcd_id[i] in same_id_dict.keys():
                    pcd_id[i] = same_id_dict[pcd_id[i]]

            # estimate position based on speed and previous position
            new_estimated_position = dict()
            for i in speed_list.keys():
                new_estimated_position[i] = estimated_position_list[i] + speed_list[i]
            estimated_position_list = new_estimated_position

            new_previous_position = dict()
            new_speed_list = dict()
            new_estimated_position_list = dict()

            for i in range(np.unique(pcd_id).shape[0]):
                instance_id = np.unique(pcd_id)[i]
                # get center position of the instance point cloud
                mask = np.where(pcd_id == instance_id)
                position = np.mean(pcd[mask], axis=0)
                # if any estimated position is close enough to the current position, merge the id
                if appeared_id.count(instance_id) == 0:
                    for j in estimated_position_list.keys():
                        #print(f"distance between {instance_id} and {j} is {np.linalg.norm(position - estimated_position_list[j])}")
                        if not j in pcd_id and np.linalg.norm(position - estimated_position_list[j]) < args.position_diff_threshold:
                            same_id_dict[instance_id] = j
                            pcd_id[mask] = j
                            instance_id = j
                            break
                # update lists
                if previous_position.get(instance_id) is not None:
                    prev_frame, prev_pos = previous_position[instance_id]
                    if speed_list.get(instance_id) is not None:
                        new_speed_list[instance_id] = (position - prev_pos) / (frame_idx - prev_frame) * args.speed_momentum + (1 - args.speed_momentum) * speed_list[instance_id]
                    else:
                        new_speed_list[instance_id] = (position - prev_pos) / (frame_idx - prev_frame)
                new_previous_position[instance_id] = (frame_idx, position)
                new_estimated_position_list[instance_id] = position
                if not instance_id in appeared_id:
                    appeared_id.append(instance_id)

            tmp = new_estimated_position_list
            for i in estimated_position_list.keys():
                if tmp.get(i) is None:
                    tmp[i] = estimated_position_list[i]
            estimated_position_list = tmp

            tmp = new_speed_list
            for i in speed_list.keys():
                if tmp.get(i) is None:
                    tmp[i] = speed_list[i]
            speed_list = tmp
            
            tmp = new_previous_position
            for i in previous_position.keys():
                if tmp.get(i) is None:
                    tmp[i] = previous_position[i]
            previous_position = tmp

        for i in range(len(pcd_id)):
            while pcd_id[i] in same_id_dict.keys():
                pcd_id[i] = same_id_dict[pcd_id[i]]
        ####################### id merge with position estimation ############################

        pcd_id_list.append(pcd_id)
        src.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=0.1, max_nn=30))
        src_list.append(src)

    if args.id_merge_with_speed and args.vis:
        for i in same_id_dict.keys():
            print(f"instance {i} is merged with {same_id_dict[i]}")

    ############### bounding box generation ################
    np_aggre_pcd_id = np.hstack(pcd_id_list).astype(np.int16)
    instance_idx_list = np.unique(np_aggre_pcd_id)
    bounding_boxes = dict()
    t_bb = dict()
    
    for idx_instance in instance_idx_list:
        instance_src_list = list()
        instance_frame_indices = list()

        for i, frame_idx in enumerate(idx_range):
            if (pcd_id_list[i] == idx_instance).sum() == 0:
                continue
            instance_frame_indices.append(frame_idx)
            single_frame_instace_pcd = np.array(src_list[i].points)[pcd_id_list[i] == idx_instance]

            single_frame_instace_pcd_color = np.array(src_list[i].colors)[pcd_id_list[i] == idx_instance]
            single_frame_instance_src = open3d.geometry.PointCloud()
            single_frame_instance_src.points = open3d.utility.Vector3dVector(single_frame_instace_pcd)
            single_frame_instance_src.colors = open3d.utility.Vector3dVector(single_frame_instace_pcd_color)
            single_frame_instance_src.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=1.0, max_nn=30))
            # make normals to be oriented to the camera
            single_frame_instance_src.orient_normals_towards_camera_location(np.array([0, 0, 0]))
            instance_src_list.append(single_frame_instance_src)

            if args.vis:
                print("                                                                            ", end="\r")
                #print(f"instance_id:{idx_instance} frame:{frame_idx}", end="\r")
                #o3d.visualization.draw_geometries([single_frame_instance_src, AXIS_PCD], point_show_normal=True)
        print()

        transformation_matrix_list = list()
        pose_graph, mean_dis = full_registration(instance_src_list)
        
        max_ptr_idx = 0
        max_ptr = 0
        for i in range(0, len(instance_src_list)):
            if len(instance_src_list[i].points) > max_ptr:
                max_ptr = len(instance_src_list[i].points)
                max_ptr_idx = i
        
        print("Optimizing PoseGraph ...")
        option = o3d.pipelines.registration.GlobalOptimizationOption(
            max_correspondence_distance=mean_dis,
            edge_prune_threshold=0.9,
            reference_node=max_ptr_idx)
        with o3d.utility.VerbosityContextManager(
                o3d.utility.VerbosityLevel.Debug) as cm:
            o3d.pipelines.registration.global_optimization(
                pose_graph,
                o3d.pipelines.registration.GlobalOptimizationLevenbergMarquardt(),
                o3d.pipelines.registration.GlobalOptimizationConvergenceCriteria(),
                option)
        transformed_src_list = copy.deepcopy(instance_src_list)
        print("Transform points and display")
        for i in range(0, len(instance_src_list)):
            transformation_matrix_list.append(pose_graph.nodes[i].pose)
            #print(pose_graph.nodes[i].pose)
            transformed_src_list[i].transform(pose_graph.nodes[i].pose)

            src1 = copy.deepcopy(instance_src_list[i]).transform(pose_graph.nodes[i].pose)
            src2 = copy.deepcopy(instance_src_list[i-1]).transform(pose_graph.nodes[i-1].pose)
            src1.paint_uniform_color([1, 0, 0])
            src2.paint_uniform_color([0, 1, 0])
            #o3d.visualization.draw_geometries_with_key_callbacks([src1, src2], {ord("B"): set_black_background, ord("W"): set_white_background })
        center_tr_matrix = copy.deepcopy(pose_graph.nodes[max_ptr_idx].pose)
        for i in range(len(transformation_matrix_list)):
            transformation_matrix_list[i] = np.linalg.inv(center_tr_matrix) @ transformation_matrix_list[i]

        global_xyz_transformed_src_list = list()
        for point_id in range(len(instance_src_list)):
            transformed_pcd = copy.deepcopy(instance_src_list[point_id]).transform(transformation_matrix_list[point_id])
            np_transformed_pcd = np.array(transformed_pcd.points)
            np_transformed_pcd_color = np.array(transformed_pcd.colors)
            global_xyz_transformed_src_list.append(transformed_pcd)

        if args.vis:
            print(f"instance_id:{idx_instance} after registration")
            #o3d.visualization.draw_geometries_with_key_callbacks(global_xyz_transformed_src_list, {ord("B"): set_black_background, ord("W"): set_white_background })

        merged_global_xyz_transformed_src = open3d.geometry.PointCloud()
        merged_global_xyz_transformed_src.points = open3d.utility.Vector3dVector(np.vstack([np.array(src.points) for src in global_xyz_transformed_src_list]))
        merged_global_xyz_transformed_src.colors = open3d.utility.Vector3dVector(np.vstack([np.array(src.colors) for src in global_xyz_transformed_src_list]))
        before_dbscan = np.array(merged_global_xyz_transformed_src.points)
        if args.dbscan_each_instance:
            dis = np.mean(np.linalg.norm(np.array(merged_global_xyz_transformed_src.points), axis=1))
            dis = dis * (0.3 * np.pi / 180) * 2.0
            if len(merged_global_xyz_transformed_src.points) < 500:
                un_noise_idx = np.arange(len(merged_global_xyz_transformed_src.points))
            elif args.dbscan_max_cluster:
                un_noise_idx = dbscan_max_cluster(merged_global_xyz_transformed_src, eps = dis, min_points=5)
            else:
                un_noise_idx = dbscan(merged_global_xyz_transformed_src, eps = dis, min_points=5)
            instance_points = np.array(merged_global_xyz_transformed_src.points)[un_noise_idx]
            instance_colors = np.array(merged_global_xyz_transformed_src.colors)[un_noise_idx]

        else:
            instance_points = np.array(merged_global_xyz_transformed_src.points)
            instance_colors = np.array(merged_global_xyz_transformed_src.colors)

        
        lidar_to_camera = np.array([[0, -1, 0],[0, 0, -1],[1,0,0]])
        camera_coord_pcd_instance = instance_points @ lidar_to_camera.T
        
        src = open3d.geometry.PointCloud()
        src.points = open3d.utility.Vector3dVector(camera_coord_pcd_instance)
        src.colors = open3d.utility.Vector3dVector(instance_colors)
        
        if len(src.points) != 0: #dbscan 이후 point가 남아있을 경우만 bounding box 얻기
            obj = get_obj(np.array(src.points), args.bbox_gen_fit_method)
        else:
            continue
        ############ camera coords to lidar #########
        line_set, box3d = translate_boxes_to_open3d_instance(obj)
        origin_line_set_lidar, original_box3d_lidar = translate_boxes_to_lidar_coords(box3d, obj.ry, lidar_to_camera)
        
        if args.vis:
            # find max_ptr_idx th frame with instance_id appears
            frame_cnt = 0
            for i in idx_range:
                if i in instance_frame_indices:
                    if frame_cnt == max_ptr_idx:
                        real_idx = i
                        break
                    frame_cnt += 1
            # load max_ptr_idx frame
            max_ptr_gt_box = np.fromfile(os.path.join(args.dataset_path,f'scene-{args.scene_idx}','annotations',f'{str(real_idx).zfill(6)}.bin')).reshape(-1, 7)
            center = np.mean(instance_points, axis=0)
            # find the closest gt box
            min_distance = 100000
            min_idx = 0
            for i in range(max_ptr_gt_box.shape[0]):
                distance = np.linalg.norm(center - max_ptr_gt_box[i, :3])
                if distance < min_distance:
                    min_distance = distance
                    min_idx = i
            before_dbscan_src = open3d.geometry.PointCloud()
            before_dbscan_src.points = open3d.utility.Vector3dVector(before_dbscan)
            before_dbscan_src.paint_uniform_color(instance_colors[0])
            line_set_gt, box3d_gt = translate_boxes_to_open3d_gtbox(max_ptr_gt_box[min_idx])
            line_set_gt.paint_uniform_color([0, 0, 1])
            print(f"bounding box for instance :{idx_instance}")
            src_lidar = open3d.geometry.PointCloud()
            src_lidar.points = open3d.utility.Vector3dVector(instance_points)
            src_lidar.colors = open3d.utility.Vector3dVector(instance_colors)
            t_obj = get_obj(np.array(src.points), 'closeness_to_edge')
            t_line_set, t_box3d = translate_boxes_to_open3d_instance(t_obj)
            t_origin_line_set_lidar, t_original_box3d_lidar = translate_boxes_to_lidar_coords(t_box3d, t_obj.ry, lidar_to_camera)
            t_origin_line_set_lidar.paint_uniform_color([0, 1, 0])
            origin_line_set_lidar.paint_uniform_color([1, 0, 0])
            o3d.visualization.draw_geometries_with_key_callbacks([src_lidar, origin_line_set_lidar, t_origin_line_set_lidar, line_set_gt, before_dbscan_src], {ord("B"): set_black_background, ord("W"): set_white_background })
        
        i = 0
        for frame_idx in idx_range:
            if frame_idx in instance_frame_indices:
                tr_matrix = transformation_matrix_list[i]
                tr_matrix = np.linalg.inv(tr_matrix)
                new_tr_mat = np.eye(4)
                rotation = np.arctan2(tr_matrix[1, 0], tr_matrix[0, 0])
                new_tr_mat[:3, :3] = R.from_euler('z', rotation).as_matrix()
                new_tr_mat[:3, 3] = copy.deepcopy(origin_line_set_lidar).transform(tr_matrix).get_center() - copy.deepcopy(origin_line_set_lidar).transform(new_tr_mat).get_center()
                line_set_lidar = copy.deepcopy(origin_line_set_lidar).transform(new_tr_mat)
                t_line_set_lidar = copy.deepcopy(t_origin_line_set_lidar).transform(new_tr_mat)
                bounding_boxes[(idx_instance, frame_idx)] = copy.deepcopy(line_set_lidar)
                t_bb[(idx_instance, frame_idx)] = copy.deepcopy(t_line_set_lidar)
                i += 1
        ############### bounding box generation ################

    if args.vis:
        max_bb_count = 0
        for frame_idx in idx_range:
            load_list = list()
            max_bb = False
            bb_count = 0
            for idx_instance in instance_idx_list:
                if (idx_instance, frame_idx) in bounding_boxes.keys():
                    line_set_lidar = bounding_boxes[(idx_instance, frame_idx)]
                    line_set_lidar.paint_uniform_color([1, 0, 0])
                    t_line_set_lidar = t_bb[(idx_instance, frame_idx)]
                    t_line_set_lidar.paint_uniform_color([0, 1, 0])
                    load_list.append(line_set_lidar)
                    load_list.append(t_line_set_lidar)
                    bb_count += 1
            if bb_count > max_bb_count:
                max_bb_count = bb_count
                max_bb = True
            full_pc_xyz = np.fromfile(os.path.join(args.dataset_path,f'scene-{args.scene_idx}','pointcloud',f'{str(frame_idx).zfill(6)}.bin'), dtype=np.float32).reshape(-1, 3)
            full_pc_xyz = full_pc_xyz[full_pc_xyz[:, 2] > args.z_threshold]
            color = np.ones_like(full_pc_xyz) * [0.5, 0.5, 0.5]
            full_pc = open3d.geometry.PointCloud()
            full_pc.points = open3d.utility.Vector3dVector(full_pc_xyz)
            full_pc.colors = open3d.utility.Vector3dVector(color)
            load_list.append(full_pc)
            load_list.append(AXIS_PCD)
            # load gt box
            gt_box = np.fromfile(os.path.join(args.dataset_path,f'scene-{args.scene_idx}','annotations',f'{str(frame_idx).zfill(6)}.bin')).reshape(-1, 7)
            for i in range(gt_box.shape[0]):
                line_set_gt, box3d_gt = translate_boxes_to_open3d_gtbox(gt_box[i])
                line_set_gt.paint_uniform_color([0, 0, 1])
                load_list.append(line_set_gt)
            if frame_idx % 10 != 0 and not max_bb:
                continue
            print(f"bounding box for frame :{frame_idx}")
            o3d.visualization.draw_geometries_with_key_callbacks(load_list, {ord("B"): set_black_background, ord("W"): set_white_background })



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='pseudo bounding generation ')
    parser.add_argument('--dataset_path', type=str, default='/workspace/3df_data/waymo_sam2')
    parser.add_argument('--visible_bbox_estimation', type=bool, default=True)
    parser.add_argument('--perform_db_scan_before_registration', type=bool, default=True)
    parser.add_argument('--with_gt_box', type=bool, default=False)
    parser.add_argument('--axis_aligned', type=bool, default=True)
    parser.add_argument('--pca', type=bool, default=True)
    parser.add_argument('--orient', type=bool, default=True)
    parser.add_argument('--vis', type=bool, default=True)
    parser.add_argument('--scene_idx', type=int,default=3)
    parser.add_argument('--src_frame_idx', type=int, default=0)
    parser.add_argument('--tgt_frame_idx', type=int, default=0)
    parser.add_argument('--rgs_start_idx',type=int, default=0)
    parser.add_argument('--rgs_end_idx',type=int, default=156)
    parser.add_argument('--origin',type=bool, default=False)
    parser.add_argument('--clustering',type=str, default='dbscan')
    parser.add_argument('--dbscan_each_instance', type=bool, default=False)
    parser.add_argument('--bbox_gen_fit_method', type=str, default='point_normal')

    parser.add_argument('--dbscan_max_cluster', type=bool, default=True)
    parser.add_argument('--id_merge_with_speed', type=bool, default=True)
    parser.add_argument('--position_diff_threshold', type=float, default=1.5)
    parser.add_argument('--speed_momentum', type=float, default=0.5)
    
    parser.add_argument('--registration_with_full_pc', type=bool, default=False)
    parser.add_argument('--z_threshold', type=float, default=0.3)

    args = parser.parse_args()
    
    if args.origin:
        source = np.fromfile(os.path.join(args.dataset_path,f'scene-{args.scene_idx}','pointcloud',f'{str(args.src_frame_idx).zfill(6)}.bin'), dtype=np.float32).reshape(-1, 3)

        src = open3d.geometry.PointCloud()
        src.points = open3d.utility.Vector3dVector(source[:, :3])
        src.paint_uniform_color([1, 0.706, 0])
        vis = open3d.visualization.Visualizer()
        vis.create_window()

        vis.get_render_option().point_size = 1.0
        vis.get_render_option().background_color = np.ones(3)


        vis.add_geometry(AXIS_PCD)
        vis.add_geometry(src)
        vis.run()    
    
    main(args)