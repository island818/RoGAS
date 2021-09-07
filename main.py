import os

import numpy

os.environ['CUDA_VISIBLE_DEVICES'] = '0'
import torch
import argparse
from model.gcn import gcn_env
from model.dqn_agent import QAgent
from copy import deepcopy
import numpy as np
torch.manual_seed(12345)
import time

parser = argparse.ArgumentParser(description='RoGAS')
parser.add_argument('--dataset', type=str, default="cresci-2015")
parser.add_argument('--folds', type=int, default=10)

parser.add_argument('--lr', type=float, default=0.05)
parser.add_argument('--weight_decay', type=float, default=0.01)
parser.add_argument('--batch_size', type=int, default=64)
parser.add_argument('--max_timesteps', type=int, default=1)
parser.add_argument('--max_episodes', type=int, default=20)

parser.add_argument('--replay_memory_size', type=int, default=10000)
parser.add_argument('--replay_memory_init_size', type=int, default=500)
parser.add_argument('--update_target_estimator_every', type=int, default=1)

parser.add_argument('--layer_num', type=int, default=3)
parser.add_argument('--width_num',type=int,default=2)
parser.add_argument('--discount_factor', type=float, default=0.95)
parser.add_argument('--epsilon_start', type=float, default=1.)
parser.add_argument('--epsilon_end', type=float, default=0.1)
parser.add_argument('--epsilon_decay_steps', type=int, default=100)
parser.add_argument('--norm_step', type=int, default=200)
parser.add_argument('--mlp_layers', type=list, default=[64, 128, 256, 128, 64])

parser.add_argument('--sg_encoder', type=str, default='GCN')
parser.add_argument('--hid_dim', type=int, default=64)
parser.add_argument('--out_dim', type=int, default=2)
args = parser.parse_args()
args.device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')

print(f"Searching with {args.layer_num} layers and {args.width_num} width")
def main(K=0):
    env = gcn_env(dataset = args.dataset, folds = args.folds,
                  max_layer = args.layer_num,
                  max_width = args.width_num,
                  hid_dim = args.hid_dim, out_dim = args.out_dim,
                  lr = args.lr, weight_decay = args.weight_decay,
                  device = args.device,
                  policy = "",K=K)
    agent = QAgent(replay_memory_size = args.replay_memory_size,
                   replay_memory_init_size = args.replay_memory_init_size,
                   update_target_estimator_every = args.update_target_estimator_every,
                   discount_factor = args.discount_factor,
                   epsilon_start = args.epsilon_start,
                   epsilon_end = args.epsilon_end,
                   epsilon_decay_steps = args.epsilon_decay_steps,
                   lr=args.lr,
                   batch_size=env.batch_size_qdn,
                   sg_num = env.sg_num,
                   layer_num=env.max_layer,
                   width_num=env.width_num,
                   norm_step=args.norm_step,
                   mlp_layers=args.mlp_layers,
                   state_shape=env.state_shape,
                   device=args.device)
    env.policy = agent
    last_val = 0.0
    # Training: Learning with RL agent
    print("Training RL agent on Validation Set")
    for i_episode in range(1, args.max_episodes + 1):
        loss1,loss2, _, (val_acc, mean_reward) = agent.learn(env, args.max_timesteps)
        if val_acc >= last_val:
            best_policy = deepcopy(agent)
        last_val = val_acc
        print("Training Meta-policy:", i_episode, "Val_Acc:", val_acc, "Avg_reward:", mean_reward)

    print("Training GNNs with learned RL agent")
    new_env = gcn_env(dataset = args.dataset, folds = args.folds,
                      max_layer=args.layer_num,
                      max_width=args.width_num,
                      hid_dim = args.hid_dim, out_dim = args.out_dim,
                      lr = args.lr, weight_decay = args.weight_decay,
                      device = args.device,
                      policy = "",K=K)
    new_env.policy = best_policy
    states = new_env.reset()
    for i_episode in range(1, 31):
        actions = new_env.policy.eval_step(states)
        states, rewards, dones, (val_acc, mean_reward) = new_env.step(actions)
        test_acc = new_env.test()
        print("Training GNN", i_episode, "; Val ACC:", val_acc, "; Test ACC:", test_acc)

if __name__ == '__main__':
    main()