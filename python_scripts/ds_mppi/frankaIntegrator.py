import sys
import zmq
sys.path.append('/functions/')
from isaac_gym_helpers import *
from MPPI import *
import torch

sys.path.append('../mlp_learn/')
from sdf.robot_sdf import RobotSdfCollisionNet

# define tensor parameters (cpu or cuda:0)
if 1:
    params = {'device': 'cpu', 'dtype': torch.float32}
else:
    params = {'device': 'cuda:0', 'dtype': torch.float32}


def main_loop(gym_instance):
    ########################################
    ###            ZMQ SETUP             ###
    ########################################
    context = zmq.Context()
    # socket to receive data from slow loop
    socket_receive_policy = context.socket(zmq.SUB)
    socket_receive_policy.setsockopt(zmq.CONFLATE, 1)
    socket_receive_policy.connect("tcp://localhost:1337")
    socket_receive_policy.setsockopt(zmq.SUBSCRIBE, b"")

    # socket to publish data to slow loop
    socket_send_state = context.socket(zmq.PUB)
    socket_send_state.bind("tcp://*:1338")

    ########################################
    ###        CONTROLLER SETUP          ###
    ########################################
    t00 = time.time()
    DOF = 7
    L = 1

    # Load nn model
    s = 256
    n_layers = 5
    skips = []
    #fname = '%ddof_sdf_%dx%d_mesh.pt' % (DOF, s, n_layers) # planar robot
    fname = 'franka_%dx%d.pt' % (s, n_layers)  # franka robot

    if skips == []:
        n_layers -= 1
    nn_model = RobotSdfCollisionNet(in_channels=DOF+3, out_channels=9, layers=[s] * n_layers, skips=skips)
    nn_model.load_weights('../mlp_learn/models/' + fname, params)
    nn_model.model.to(**params)
    # prepare models: standard (used for AOT implementation), jit, jit+quantization
    nn_model.model_jit = nn_model.model
    nn_model.model_jit = torch.jit.script(nn_model.model_jit)
    nn_model.model_jit = torch.jit.optimize_for_inference(nn_model.model_jit)
    nn_model.update_aot_lambda()
    #nn_model.model.eval()
    # Initial state
    q_0 = torch.tensor([-1.2, -0.08, 0, -2, -0.16,  1.6, -0.75]).to(**params)
    q_f = torch.tensor([1.2, -0.08, 0, -2, -0.16,  1.6, -0.75]).to(**params)

    # Robot parameters
    dh_a = torch.tensor([0, 0, 0, 0.0825, -0.0825, 0, 0.088, 0])        # "r" in matlab
    dh_d = torch.tensor([0.333, 0, 0.316, 0, 0.384, 0, 0, 0.107])       # "d" in matlab
    dh_alpha = torch.tensor([0, -pi/2, pi/2, pi/2, -pi/2, pi/2, pi/2, 0])  # "alpha" in matlab
    dh_params = torch.vstack((dh_d, dh_a*0, dh_a, dh_alpha)).T.to(**params)          # (d, theta, a (or r), alpha)
    # Obstacle spheres (x, y, z, r)
    # T-bar
    t1 = torch.tensor([0.4, -0.3, 0.75, .04])
    t2 = t1 + torch.tensor([0, 0.6, 0, 0])
    top_bar = t1 + torch.linspace(0, 1, 10).reshape(-1, 1) * (t2 - t1)
    t3 = t1 + 0.5 * (t2 - t1)
    t4 = t3 + torch.tensor([0, 0, -0.65, 0])
    middle_bar = t3 + torch.linspace(0, 1, 10).reshape(-1, 1) * (t4 - t3)
    bottom_bar = top_bar - torch.tensor([0, 0, 0.65, 0])
    #obs = torch.vstack((top_bar, middle_bar, bottom_bar))
    #obs = torch.vstack((middle_bar, bottom_bar))
    obs = middle_bar

    n_dummy = 1
    dummy_obs = torch.hstack((torch.zeros(n_dummy, 3)+10, torch.zeros(n_dummy, 1)+0.1)).to(**params)
    obs = torch.vstack((obs, dummy_obs)).to(**params)

    # Integration parameters
    A = -1 * torch.diag(torch.ones(DOF)).to(**params)
    N_traj = 1
    dt_H = 3
    dt = 1
    dt_sim = 0.1
    N_ITER = 0

    #set up second mppi to move the robot
    mppi_step = MPPI(q_0, q_f, dh_params, obs, dt_sim, 2, 1, A, dh_a, nn_model)
    mppi_step.Policy.alpha_s *= 0

    # ########################################
    # ###     GYM AND SIMULATION SETUP     ###
    # ########################################
    world_instance, robot_sim, robot_ptr, env_ptr = deploy_world_robot(gym_instance, params)
    w_T_r = copy.deepcopy(robot_sim.spawn_robot_pose)
    R_tens = getTensTransform(w_T_r)

    obs_list = []
    for i, sphere in enumerate(obs):
        tmpObsDict = deploy_sphere(sphere, gym_instance, w_T_r, 'sphere_%d'%(i), params)
        obs_list.append(tmpObsDict)

    ########################################
    ###     RUN MPPI AND SIMULATE        ###
    ########################################
    # warmup gym and jit
    for i in range(20):
        mppi_step.Policy.sample_policy()
        _, _, _ = mppi_step.propagate()
        gym_instance.step()
        robot_sim.set_robot_state(mppi_step.q_cur, mppi_step.q_cur*0, env_ptr, robot_ptr)
        numeric_fk_model(mppi_step.q_cur, dh_params, 10)
    best_idx = -1
    print('Init time: %4.2fs' % (time.time() - t00))
    time.sleep(1)
    t0 = time.time()
    all_fk_kernel = []
    while torch.norm(mppi_step.q_cur - q_f)+1 > 0.001:
        t_iter = time.time()
        # [ZMQ] Receive policy from planner
        # mppi_step.Policy.n_kernels = mppi.Policy.n_kernels
        # mppi_step.Policy.mu_c = mppi.Policy.mu_c
        # mppi_step.Policy.sigma_c = mppi.Policy.sigma_c
        try:
            data = socket_receive_policy.recv_pyobj(zmq.DONTWAIT)
            mppi_step.Policy.n_kernels = data[0]
            mppi_step.Policy.mu_c[0:mppi_step.Policy.n_kernels] = data[1]
            mppi_step.Policy.alpha_c[0:mppi_step.Policy.n_kernels] = data[2]
            mppi_step.Policy.sigma_c[0:mppi_step.Policy.n_kernels] = data[3]
            mppi_step.Policy.mu_c[mppi_step.Policy.n_kernels:] *= 0
            mppi_step.Policy.alpha_c[mppi_step.Policy.n_kernels:] *= 0
            mppi_step.Policy.sigma_c[mppi_step.Policy.n_kernels:] *= 0
            #print(f"Received policy {iter} \n {data}")
            #print(info)
        except:
            pass

        if len(all_fk_kernel) != mppi_step.Policy.n_kernels:
            all_fk_kernel = []
            for mu in mppi_step.Policy.mu_c[0:mppi_step.Policy.n_kernels]:
                kernel_fk, _ = numeric_fk_model(mu, dh_params, 3)
                fk_arr = kernel_fk.flatten(0, 1) @ R_tens[0:3, 0:3] + R_tens[0:3, 3]
                all_fk_kernel.append(fk_arr)

        # Propagate modulated DS


        gym_instance.clear_lines()
        # Update current robot state

        # Update current robot state
        mppi_step.Policy.sample_policy()    # samples a new policy using planned means and sigmas
        _, _, _ = mppi_step.propagate()
        mppi_step.q_cur = mppi_step.q_cur + mppi_step.qdot[0, :] * dt_sim

        # [ZMQ] Send current state to planner
        socket_send_state.send_pyobj(mppi_step.q_cur)

        goal_fk, _ = numeric_fk_model(q_f, dh_params, 2)
        # # draw lines in gym
        # draw kernels
        for fk in all_fk_kernel:
            gym_instance.draw_lines(fk, color=[1, 1, 1])
        gym_instance.draw_lines(goal_fk.flatten(0, 1) @ R_tens[0:3, 0:3] + R_tens[0:3, 3], color=[0.6, 1, 0.6])

        # # draw best trajectory
        # best_idx = torch.argmin(cost)
        # all_fk_traj, _ = numeric_fk_model_vec(mppi.all_traj[best_idx:best_idx+1].view(-1, 7), dh_params, 10)
        # all_fk_traj = all_fk_traj.view(1, -1, 3) @ R_tens[0:3, 0:3] + R_tens[0:3, 3]
        # for fk in all_fk_traj:
        #     gym_instance.draw_lines(fk, color=[1, 1, 1])

        # gym_instance.step()
        q_des = mppi_step.q_cur
        dq_des = mppi_step.qdot[0, :] * 0
        robot_sim.set_robot_state(q_des, dq_des, env_ptr, robot_ptr)

        N_ITER += 1
        if N_ITER > 10000:
            break
        # print(q_cur)
        t_iter = time.time() - t_iter
        time.sleep(max(0.0, 1/60 - t_iter))
        print(f'Iteration:{N_ITER:4d}, Time:{t_iter:4.2f}, Frequency:{1/t_iter:4.2f},',
              f' Avg. frequency:{N_ITER/(time.time()-t0):4.2f}',
              f' Kernel count:{mppi_step.Policy.n_kernels:4d}')
        #print('Position difference: %4.3f'% (mppi_step.q_cur - q_f).norm().cpu())
    td = time.time() - t0
    print('Time: ', td)
    print('Time per iteration: ', td / N_ITER, 'Hz: ', 1 / (td / (N_ITER)))
    print('Time per rollout: ', td / (N_ITER * N_traj))
    print('Time per rollout step: ', td / (N_ITER * N_traj * dt_H))
    #print(torch_profiler.key_averages().table(sort_by="cpu_time_total", row_limit=20))
    time.sleep(10)


if __name__ == '__main__':
    # instantiate empty gym:
    sim_params = load_yaml(join_path(get_gym_configs_path(), 'physx.yml'))
    gym_instance = Gym(**sim_params)
    main_loop(gym_instance)

