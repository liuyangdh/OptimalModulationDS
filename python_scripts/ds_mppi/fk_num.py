import torch
import numpy as np
from plots import *

def dh_transform(q, d, theta, a, alpha):
    """
    Denavit-Hartenberg transformation matrix.
    """
    # Compute the transformation matrix
    sa = torch.sin(alpha)
    ca = torch.cos(alpha)
    sq = torch.sin(q+theta)
    cq = torch.cos(q+theta)
    T = torch.tensor([
        [cq,        -sq,    0,      a],
        [sq * ca,   cq*ca,  -sa,    -d*sa],
        [sq * sa,   cq*sa,  ca,     d*ca],
        [0,         0,      0,      1]
    ])
    return T


def dh_fk(q, dh_params):
    """
    Forward kinematics for a robot with Denavit-Hartenberg parameters.
    """
    # Initialize the transformation matrix
    T = [torch.eye(4)]
    # Loop through each joint
    for i in range(len(q)):
        # Extract the parameters for this joint
        d, theta, a, alpha = dh_params[i]
        # Compute the transformation for this joint
        T_prev = T[-1]
        T.append(T_prev @ dh_transform(q[i], d, theta, a, alpha))
    return T


def numeric_fk_model(q, dh_params, n_pts):
    """
    Caclulate positions of points on the robot arm.
    """
    # Compute the transformation matrices
    n_dof = len(q)
    a = dh_params[:, 2]
    P_arr = dh_fk(q, dh_params)
    robot = dict()
    robot['links'] = []
    robot['pts_int'] = []
    # Initialize the points array
    # Loop through each joint
    for i in range(n_dof):
        p0 = torch.tensor([0, 0, 0])
        p1 = torch.tensor([a[i+1], 0, 0])
        lspan = torch.linspace(0, 1, n_pts).unsqueeze(1)
        v = torch.tile(p0, (n_pts, 1)) + torch.tile(p1, (n_pts, 1)) * lspan
        R = P_arr[i + 1][:3, :3]
        T = P_arr[i + 1][:3, 3]
        # Compute the position of the point for this link
        pts = (R @ v.transpose(0, 1)).transpose(0, 1) + T
        robot['links'].append(pts)
        robot['pts_int'].append(v)
    return robot


def dist_to_point(robot, y):
    """
    Calculate the distance between the robot links and a point in task space.
    """
    mindists = []
    minidxs = []
    res = dict()
    for link_pts in robot['links']:
        dist = np.linalg.norm(link_pts - y, 2, 1)
        minidx = np.argmin(dist)
        mindists.append(dist[minidx])
        minidxs.append(minidx)
    minidx = np.argmin(mindists)
    res['mindist'] = mindists[minidx]
    res['linkidx'] = minidx
    res['ptidx'] = minidxs[minidx]
    return res



def main():
    params = {'device': 'cpu', 'dtype': torch.float32}
    q = torch.tensor([0, 0]).to(**params)
    dh_a = torch.tensor([0, 3, 3])
    dh_alpha = dh_a*0
    dh_d = dh_a*0
    dh_theta = dh_a*0
    dh_params = (torch.vstack((dh_d, dh_theta, dh_a, dh_alpha)).T).to(**params)
    robot = numeric_fk_model(q, dh_params, 10)
    link_pts = robot['links']
    r_h = init_robot_plot(link_pts, -10, 10, -10, 10)
    c_h = plot_circ(np.array([8, 5]), 1)
    for i in range(300):
        q = q+torch.tensor([0.01, 0.01]).to(**params)
        robot = numeric_fk_model(q, dh_params, 10)
        upd_r_h(robot['links'], r_h)
        dst = dist_to_point(robot, np.array([10, 0, 0]))
        print(dst)
        c_h.set_center(q)
        plt.pause(0.0001)

if __name__ == '__main__':
    main()



