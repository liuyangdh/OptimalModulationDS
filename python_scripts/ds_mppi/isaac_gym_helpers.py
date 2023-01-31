import copy, time, argparse, os, sys, math, numpy as np
from isaacgym import gymapi
from isaacgym import gymutil
from quaternion import from_euler_angles, as_float_array, as_rotation_matrix, from_float_array, as_quat_array
from torch import pi

from storm_kit.gym.core import Gym, World
from storm_kit.gym.sim_robot import RobotSim
from storm_kit.util_file import *
from storm_kit.differentiable_robot_model.coordinate_transform import quaternion_to_matrix, CoordinateTransform

import torch

## create robot simulation
def deploy_world_robot(gym_instance, params):
    robot_yml = 'content/configs/gym/franka.yml'
    sim_params = load_yaml(robot_yml)['sim_params']
    sim_params['asset_root'] = os.path.dirname(__file__) + '/content/assets'
    sim_params['collision_model'] = None
    # task_file = 'franka_reacher_env2.yml'
    gym = gym_instance.gym
    sim = gym_instance.sim
    robot_sim = RobotSim(gym_instance=gym_instance.gym, sim_instance=gym_instance.sim, **sim_params,
                         device=params['device'])
    robot_pose = sim_params['robot_pose']
    env_ptr = gym_instance.env_list[0]
    robot_ptr = robot_sim.spawn_robot(env_ptr, robot_pose, coll_id=2)

    # create world
    world_yml = 'content/configs/gym/collision_demo.yml'
    world_params = load_yaml(world_yml)
    # get pose
    w_T_r = copy.deepcopy(robot_sim.spawn_robot_pose)

    w_T_robot = torch.eye(4)
    quat = torch.tensor([w_T_r.r.w, w_T_r.r.x, w_T_r.r.y, w_T_r.r.z]).unsqueeze(0)
    rot = quaternion_to_matrix(quat)
    w_T_robot[0, 3] = w_T_r.p.x
    w_T_robot[1, 3] = w_T_r.p.y
    w_T_robot[2, 3] = w_T_r.p.z
    w_T_robot[:3, :3] = rot[0]
    world_instance = World(gym, sim, env_ptr, world_params, w_T_r=w_T_r)
    return world_instance, robot_sim, robot_ptr, env_ptr