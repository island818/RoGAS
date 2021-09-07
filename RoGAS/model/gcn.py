import os
import numpy as np
import random
import torch
from torch_geometric.nn import GCNConv,global_mean_pool,GATConv
from torch_geometric.utils.convert import to_scipy_sparse_matrix
import torch.nn.functional as F
import networkx as nx
from sklearn.preprocessing import normalize
import pickle
import math
from collections import defaultdict
from .SocialData import SocialBotDataset
from sklearn import preprocessing
from .utils import remove_self_loops
from itertools import permutations
random.seed(12345)
torch.manual_seed(12345)

class Net(torch.nn.Module):
	def __init__(self, max_layer, node_dim, hid_dim, out_dim):
		super(Net, self).__init__()
		torch.manual_seed(12345)
		self.hidden = []
		self.lin_res = torch.nn.Linear(node_dim, hid_dim)
		self.hidden.append(GCNConv(node_dim, hid_dim))
		for i in range(max_layer-1):
			self.hidden.append(GCNConv(hid_dim, hid_dim))
		self.node_dim, self.hid_dim = node_dim, hid_dim
		self.fc1 = torch.nn.Linear(hid_dim, hid_dim)
		self.fc2 = torch.nn.Linear(hid_dim, hid_dim)
		self.pool = torch.mean
		self.attention = GATConv(hid_dim,hid_dim,heads=1,add_self_loops=True)
		self.lin = torch.nn.Linear(hid_dim, out_dim)

	def forward(self, action, datas,all_features,indexes,k_hop_sg,device):
		target_feats = torch.FloatTensor(len(indexes), self.hid_dim).to(device)
		if len(indexes)==1:
			sub_graph_edge_index = [(0,0)]
		else:
			sub_graph_edge_index = list(permutations(range(len(indexes)), 2))
		sub_graph_edge_index = torch.tensor(sub_graph_edge_index,dtype=torch.long).t()
		for i,(index,act2) in enumerate(datas):
			feature_index, edge_index = k_hop_sg[act2][index.item()]
			features = all_features[feature_index]
			edge_index = edge_index.to(device)
			x = features.to(device)
			x1 = F.dropout(F.relu(self.lin_res(x)),p=0.5, training=self.training)
			for k in range(action+1):
				x = F.relu(self.hidden[k].to(device)(x,edge_index),inplace=True)
				x = F.dropout(x, training=self.training)
			x = F.dropout(F.relu(self.fc1(torch.add(x,x1))), training=self.training)
			target_feats[i] = self.pool(x, dim=0,keepdim=False)  # [batch_size, hidden_channels]

		# target_feats = self.attention(target_feats.to(device),sub_graph_edge_index.to(device))
		target_feats = self.lin(target_feats)
		return F.log_softmax(target_feats, dim=1)



class gcn_env(object):
	def __init__(self,
				 dataset, folds,
				 max_layer,
				 max_width,
				 hid_dim, out_dim,
				 lr, weight_decay,
				 device,
				 policy="",
				 K=0):
		self.device = device
		self.max_layer = max_layer
		self.width_num = max_width
		self.load_social_dataset(dataset,K)
		self.train_num, self.val_num, self.test_num\
			= len(self.train_indexes), len(self.val_indexes), len(self.test_indexes)

		self.batch_size = min(self.train_num,self.val_num,self.test_num)
		self.sg_num = self.dataset.data.y.shape[0]
		self.ini_k_hop_target_user(max_width)
		self.model = Net(max_layer, self.dataset.data.x.shape[-1], hid_dim, out_dim).to(device)
		self.optimizer = torch.optim.Adam(self.model.parameters(), lr, weight_decay=weight_decay)


		self.batch_size_qdn = math.ceil(self.train_num)
		self.policy = policy
		self.state_shape = self.dataset.data.x.shape
		self.baseline_experience = 100
		self.buffers = defaultdict(list)
		self.past_performance = [0]
		self.criterion = torch.nn.CrossEntropyLoss()

	def ini_k_hop_target_user(self,max_hop):
		sp_adj = to_scipy_sparse_matrix(self.data.edge_index).tocsr()
		dd = sp_adj[:self.sg_num,:]
		self.target_user_k_adjs = []
		target_adj = dd[:, :self.sg_num]
		target_adj = target_adj.toarray()
		target_adj = normalize(target_adj, norm='l1', axis=1)
		self.target_user_k_adjs.append(target_adj)
		for hop in range(max_hop-1):
			dd = dd * sp_adj
			target_adj = dd[:,:self.sg_num]
			target_adj = target_adj.toarray()
			target_adj = normalize(target_adj, norm='l1', axis=1)
			self.target_user_k_adjs.append(target_adj)

	def load_social_dataset(self,dataset,K):
		print("loading dataset")
		min_max_scaler = preprocessing.MinMaxScaler()
		self.dataset = SocialBotDataset(root="./data", pre_transform=min_max_scaler.fit_transform,K=K)
		self.data = self.dataset[0]
		self.train_indexes, self.val_indexes, self.test_indexes,self.G = self.dataset.train_index,self.dataset.val_index,self.dataset.test_index,self.dataset.G
		self.k_hop_sg = [[] for i in range(self.width_num)]
		self.init_states = []
		self.all_target_index = list(range(len(self.dataset.data.y)))
		filepath = os.path.join("data","raw",self.dataset.cur_dataset+str(self.width_num)+"sub_g_features.pickle")
		if os.path.exists(filepath):
			with open(filepath, 'rb') as f:
				self.init_states, self.k_hop_sg = pickle.load(f)
		else:
			for item in self.all_target_index:
				print(item)
				sub_graph = nx.ego_graph(self.G,item,radius=1,center=True,undirected=False)
				edges, feature_index, features = self.map_subgraph_into_new_nodes(sub_graph, include_features=True)
				init_state = torch.mean(features, dim=0)
				self.init_states.append(init_state.numpy())
				self.k_hop_sg[0].append((feature_index, edges))
				for i in range(1,self.width_num):
						sub_graph = nx.ego_graph(self.G, item, radius=i+1, center=True, undirected=False)
						edges, feature_index, features = self.map_subgraph_into_new_nodes(sub_graph, include_features=False)
						self.k_hop_sg[i].append((feature_index,edges))
			with open(filepath,'wb') as f:
				pickle.dump([self.init_states,self.k_hop_sg], f)
		self.init_states = np.array(self.init_states)
		print("done!")

	def map_subgraph_into_new_nodes(self,G,include_features=False):
		nodes = G.nodes
		nodes_dict = { index:i for i, index in enumerate(nodes)}
		feature_index = torch.tensor(list(nodes_dict.keys()),dtype=torch.long)
		edges = [(nodes_dict[edge[0]], nodes_dict[edge[1]]) for edge in G.edges]
		edges = np.array(edges).T
		edges = remove_self_loops(edges)
		edges = torch.tensor(edges, dtype=torch.long)
		if include_features:
			features = self.data.x[feature_index]
			return edges,feature_index,features
		else:
			return edges,feature_index,None


	def reset(self,train_gnn=False):
		states = self.init_states[self.train_indexes] #(1272, 64)
		self.optimizer.zero_grad()
		return states

	def stochastic_k_hop(self, actions, index):
		next_batch = []
		target_users = np.array([i for i in range(self.sg_num)])
		for act, idx in zip(actions, index):
			prob = self.target_user_k_adjs[act][idx]
			prob = prob if np.sum(prob) > 0. else np.full(len(prob), 1. / len(prob))
			next_target = np.random.choice(target_users, p=prob)
			next_batch.append(next_target)
		return next_batch

	def step(self, actions):
		action1s = actions[0]
		action2s = actions[1]
		self.model.train()
		self.optimizer.zero_grad()
		index = self.train_indexes
		done = False

		for act1,act2, idx in zip(action1s,action2s,index):
			self.buffers[act1].append((idx,act2))
			if len(self.buffers[act1]) >= self.batch_size_qdn:
				self.train(act1, self.buffers[act1])
				self.buffers[act1] = []
				done = True

		# next states
		next_batch_index = self.stochastic_k_hop(action2s, index)
		next_states = self.init_states[next_batch_index]
		val_acc_dict = self.eval()
		val_acc = [val_acc_dict[a] for a in action1s]
		baseline = np.mean(np.array(self.past_performance[-self.baseline_experience:]))
		self.past_performance.extend(val_acc)
		reward = [100 * (each - baseline) for each in val_acc]
		r = np.mean(np.array(reward))
		val_acc = np.mean(val_acc)
		return next_states, reward, [done] * len(next_states), (val_acc, r)

	def train(self, act1, datas):
		self.model.train()
		indexes = []
		for (index,act2) in datas:
			indexes.append(index)
		length = len(indexes)
		num_batches = math.ceil(length / self.batch_size)
		for batch in range(num_batches):
			i_start = batch * self.batch_size
			i_end = min((batch + 1) * self.batch_size, length)
			preds = self.model(act1, datas[i_start:i_end], self.data.x, indexes[i_start:i_end],
								self.k_hop_sg, self.device)
			labels = torch.LongTensor(self.dataset.data.y[torch.LongTensor(indexes[i_start:i_end])]).to(self.device)
			self.criterion(preds, labels).backward()
			self.optimizer.step()

	def eval(self):
		self.model.eval()
		batch_dict = {}
		val_indexes = self.val_indexes
		val_states = self.init_states[self.val_indexes]
		val_act1s,val_act2s = self.policy.eval_step(val_states)
		s_a = zip(val_indexes, val_act1s,val_act2s)
		for i, a1, a2 in s_a:
			if a1 not in batch_dict.keys():
				batch_dict[a1] = []
			batch_dict[a1].append((i,a2))

		accs = {a: 0.0 for a in range(self.max_layer)}
		for act1 in batch_dict.keys():
			indexes = []
			for (index,act2) in batch_dict[act1]:
				indexes.append(index)
			length = len(indexes)
			correct = 0
			num_batches = math.ceil(length / self.batch_size)
			for batch in range(num_batches):
				i_start = batch * self.batch_size
				i_end = min((batch + 1) * self.batch_size, length)
				logits = self.model(act1, batch_dict[act1][i_start:i_end], self.data.x, indexes[i_start:i_end],
									self.k_hop_sg, self.device)
				preds = logits.argmax(dim=1)
				batch_label = torch.LongTensor(self.dataset.data.y[torch.LongTensor(indexes[i_start:i_end])]).to(self.device)
				correct += int((preds == batch_label).sum())  # Check against ground-truth labels.
				# logits = self.model(act1, batch_dict[act1], self.data.x, indexes, self.k_hop_sg, self.device)
			acc = correct / length
			accs[act1] = acc
			# acc[a] = preds.eq(labels).sum().item() / len(indexes)
		return accs


	def test(self):
		self.model.eval()
		batch_dict = {}
		test_indexes = self.test_indexes

		test_states = self.init_states[self.test_indexes]
		test_act1s, test_act2s = self.policy.eval_step(test_states)

		s_a = zip(test_indexes, test_act1s,test_act2s)
		for i, a1, a2 in s_a:
			if a1 not in batch_dict.keys():
				batch_dict[a1] = []
			batch_dict[a1].append((i,a2))
		test_length = len(self.test_indexes)
		correct = 0
		for act1 in batch_dict.keys():
			indexes = []
			for (index,act2) in batch_dict[act1]:
				indexes.append(index)
			length = len(indexes)
			num_batches = math.ceil(length / self.batch_size)

			for batch in range(num_batches):
				i_start = batch * self.batch_size
				i_end = min((batch + 1) * self.batch_size, length)
				logits = self.model(act1, batch_dict[act1][i_start:i_end], self.data.x, indexes[i_start:i_end], self.k_hop_sg, self.device)
				preds = logits.argmax(dim=1)
				batch_label = torch.LongTensor(self.dataset.data.y[torch.LongTensor(indexes[i_start:i_end])]).to(self.device)
				correct += int((preds == batch_label).sum())  # Check against ground-truth labels.
		acc = correct / test_length  # Derive ratio of correct predictions.
		return acc





