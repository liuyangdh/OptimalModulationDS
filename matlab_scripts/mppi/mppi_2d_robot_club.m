clc
clear all
close all force
addpath('../planar_robot_2d/')
rng('default')

%% obstacle
load('../planar_robot_2d/data/net50_pos_thr.mat')
[y_f, dy_f] = tanhNN(net);
obs_pos = [7 0]';
obs_r = 1;

%% robot
r = [4 4 0];
d = [0 0 0];
alpha = [0 0 0];
base = eye(4);
k1 = 0.9; k2 = 0.9;
q_min = [-k1*pi, -k2*pi, -10,-10];
q_max = [k1*pi, k2*pi, 10, 10];
box = [q_min(1:2); q_max(1:2)];
u_box = 2*[-1, 1; -1 1]';
pos_init = [0.5*pi, 0.5*pi]';
pos_goal = [-0.5*pi, -0.5*pi]';

%figure
f_anim = figure('Name','Animation','Position',[100 100 1400 400]);
ax_anim = subplot(1,2,1);
axis equal
title('Planar robot')
xlabel('x, m')
ylabel('y, m')

axes(ax_anim)
ax_anim.XLim = [-12; 12];
ax_anim.YLim = [-12; 12];
hold on
robot_h = create_r(ax_anim,pos_init,r,d,alpha,base);

[xc, yc] = circle_pts(obs_pos(1),obs_pos(2),obs_r-0.01);
crc_h = plot(ax_anim, xc, yc, 'r-','LineWidth',1.5);

%% Constants and Parameters
N_ITER = 1000;
H = 50;
D = 2;
SIGMA = [1, 0.5, 0.1];
N_POL = size(SIGMA,2);
MU_ARR = zeros(D, H, N_POL);

dT = 0.1;
N_TRAJ = 50;
gamma_vec = flip([0.98.^linspace(1,H-1,H-1), 0.98^H]);
beta = 0.9;
mu_alpha = 0.9;
%% lambdas
get_norm_samples = @(MU_ARR, SIGMA, N_TRAJ)...
                        normrnd(repmat(MU_ARR,[1,1,N_TRAJ]), SIGMA);
INT_MAT = tril(ones(H));
get_rollout = @(pos_init, u_sampl, dT) pos_init + ...
              pagetranspose(pagemtimes(dT*INT_MAT,pagetranspose(u_sampl)));

calc_reaching_cost = @(rollout, goal) squeeze(vecnorm(rollout - goal,2,1))';
obs_dist = @(rollout, obs_pos, obs_r) squeeze(vecnorm(rollout - obs_pos,2,1))'-obs_r;
w_fun = @(cost) exp(-1/beta * sum(gamma_vec .* cost,2));

%% plot preparation
ax_proj = subplot(1,2,2);
axis equal
title('2d MPPI vis')
xlabel('q1')
ylabel('q2')
set(gcf,'color','w');
set(gca,'fontsize',14)
hold on
%joint-space
x1_span=linspace(box(1,1),box(2,1),50); 
x2_span=linspace(box(1,2),box(2,2),50); 
[X1_mg,X2_mg] = meshgrid(x1_span, x2_span);
x=[X1_mg(:) X2_mg(:)]';

%nominal ds
% obstacle
inp = [x ; repmat(obs_pos,[1,length(x)])];
val = y_f([inp; sin(inp); cos(inp)]);
Z_mg = reshape(val,size(X1_mg));
%distance heatmap and contour
[~, obs_h] = contourf(ax_proj, X1_mg,X2_mg,Z_mg,100,'LineStyle','none');
[~, obs2_h] = contour(ax_proj, X1_mg,X2_mg,Z_mg,[obs_r,obs_r+0.01],'LineStyle','-','LineColor','k','LineWidth',2);

ax_proj.XLim = box(:,1);
ax_proj.YLim = box(:,2);
plot(pos_init(1),pos_init(2),'b*')
plot(pos_goal(1),pos_goal(2),'r*')
r_h_arr = zeros(N_POL, N_TRAJ);
for i = 1:1:N_POL
    for j = 1:1:N_TRAJ
        r_h = plot(ax_proj,zeros(1,H), zeros(1,H));
        r_h.Color=[1-1/i,1-1/i,1/i,0.5];
        r_h_arr(i, j) = r_h;
    end
end
best_traj_h = plot(ax_proj,zeros(1,H), zeros(1,H));
best_traj_h.Color=[0,1,0.5,1];
best_traj_h.LineWidth = 2;
cur_pos_h = plot(0,0,'r*');
%% MPPI

cur_pos = pos_init;
cur_vel = pos_init*0;
for i = 1:1:N_ITER
    best_cost_iter = 1e10;
    for j = 1:1:N_POL
        tic
        % sample controls (v or u)
        u = get_norm_samples(MU_ARR(:,:,j),SIGMA(j),N_TRAJ);
        % inject zero sample
        u(:,:,end) = u(:,:,end)*0;
        %inject slowdown (for acceleration control)
        ss = 5;
        u(:,1:ss,end) = u(:,1:ss,end) - cur_vel/dT/ss;
        %calculate rollouts
        v_rollout = get_rollout(cur_vel, u, dT);
        %v_rollout = u;
        rollout = get_rollout(cur_pos, v_rollout, dT);
        %calculate trajectory costs
        cost_p = calc_reaching_cost(rollout, pos_goal);
        cost_v = calc_reaching_cost(v_rollout, cur_vel*0);
        cost_lim = calc_lim_cost(rollout, box(1,:), box(2,:));
        u_lim_cost = calc_lim_cost(u, u_box(1,:), u_box(2,:));
        v_lim_cost = calc_lim_cost(v_rollout, u_box(1,:), u_box(2,:));
        %smooth_cost = calc_smooth_cost(rollout);
        dists = calc_nn_dists(y_f, rollout, obs_pos, obs_r);
        coll_cost = 1000*dist_cost(dists, 0.1, 0.2);
        cost = cost_p+...
               cost_lim+...
               u_lim_cost+...
               v_lim_cost+...
               coll_cost;
        %calculate trajectory weights
        w = w_fun(cost);
        w = w/sum(w);
        [best_cost,best_idx] = max(w);
        %update sampling means
        w_tens = reshape(repmat(w,[1,H])', [1, H ,N_TRAJ]);
        if j>0
            MU_ARR(:,:,j) = (1-mu_alpha)*MU_ARR(:,:,j)+mu_alpha*sum(w_tens.*u,3);
        end
        %shift sampling means
        MU_ARR(:,1:end-1,:) =  MU_ARR(:,2:end,:);
        %plots
        for i_traj = 1:1:N_TRAJ
            h_tmp = handle(r_h_arr(j, i_traj));
            h_tmp.XData = rollout(1,:,i_traj);
            h_tmp.YData = rollout(2,:,i_traj);
        end
        if best_cost < best_cost_iter
            cur_pos = rollout(:,1,best_idx);
            cur_vel = v_rollout(:,1,best_idx);
            cur_pos_h.XData = cur_pos(1);
            cur_pos_h.YData = cur_pos(2);
            best_cost_iter = best_cost;
            best_traj_h.XData = rollout(1,:,best_idx);
            best_traj_h.YData = rollout(2,:,best_idx);
        end
        toc
        move_r(robot_h,cur_pos,r,d,alpha,base);
        pause(0.05)
        drawnow
        frame = getframe(1);
        im = frame2im(frame);
        [imind,cm] = rgb2ind(im,256);
        filename = 'gif1.gif';
        if i == 1
          imwrite(imind,cm,filename,'gif', 'Loopcount',inf);
        else
          imwrite(imind,cm,filename,'gif','WriteMode','append');
        end

    end
    best_cost
end






%% functions
function cost = calc_lim_cost(traj, v_min, v_max)
    cost = zeros(size(traj,3), size(traj,2));
    min_tens = repmat(v_min',1,size(traj,2),size(traj,3));
    max_tens = repmat(v_max',1,size(traj,2),size(traj,3));
    cost = squeeze(any(traj<min_tens,1))' | squeeze(any(traj>max_tens,1))';
%     for i = 1:1:length(v_min)
%         cost = cost | squeeze(traj(i,:,:)<v_min(i) | traj(i,:,:)>v_max(i))';
%     end
    cost = double(cost);
end

function dst = calc_nn_dists(y_f, rollout, obs_pos, obs_r)
    r_tmp = reshape(rollout,size(rollout,1),[]);
    inp = [r_tmp; repmat(obs_pos,1,size(r_tmp,2))];
    dst = y_f([inp; sin(inp); cos(inp)])- obs_r;
    dst = reshape(dst,[],size(rollout,3))';
end

function cost = dist_cost(dst, thr0, thr1)
    cost = dst;
    idx_0 = dst > thr1; % no cost for above thr1
    idx_1 = dst < thr0; % positive cost for below thr0
    idx_smooth = ~(idx_0 | idx_1);
    cost(idx_0) = 0;
    cost(idx_1) = 1;
    cost(idx_smooth) = 1-cost(idx_smooth)./thr1;
end


function cost = calc_smooth_cost(traj)
    d_traj = diff(traj,1,2);
    cost = squeeze(vecnorm(d_traj,2,1));
    cost(end+1,:) = 0*cost(end,:);
    cost = 10*cost';
end


%robot and plotting
function pts = calc_fk(j_state,r,d,alpha,base)
    P = dh_fk(j_state,r,d,alpha,base);
    pts = zeros(3,3);
    for i = 1:1:3
        v = [0,0,0];
        R = P{i}(1:3,1:3);
        T = P{i}(1:3,4);
        p = v*R'+T';
        pts(i,:) = p;
    end
end

function handle = create_r(ax_h,j_state,r,d,alpha,base)
    pts = calc_fk(j_state,r,d,alpha,base);
    handle = plot(ax_h,pts(:,1),pts(:,2),'LineWidth',2,...
        'Marker','o','MarkerFaceColor','k','MarkerSize',4);
end

function move_r(r_handle,j_state,r,d,alpha,base)
    pts = calc_fk(j_state,r,d,alpha,base);
    r_handle.XData = pts(:,1);
    r_handle.YData = pts(:,2);
end

function [xc,yc] = circle_pts(x,y,r)
    hold on
    th = linspace(0,2*pi,50);
    xc = r * cos(th) + x;
    yc = r * sin(th) + y;
end





