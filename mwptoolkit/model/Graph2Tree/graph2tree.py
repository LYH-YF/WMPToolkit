import copy
import itertools
import torch
import stanza
from torch import nn

from mwptoolkit.module.Encoder.graph_based_encoder import GraphBasedEncoder
from mwptoolkit.module.Embedder.basic_embedder import BaiscEmbedder
from mwptoolkit.module.Decoder.tree_decoder import TreeDecoder
from mwptoolkit.module.Layer.tree_layers import NodeGenerater,SubTreeMerger,TreeNode,TreeEmbedding
from mwptoolkit.module.Strategy.beam_search import TreeBeam


class Graph2Tree(nn.Module):
    def __init__(self,config):
        super(Graph2Tree,self).__init__()
        #parameter
        self.hidden_size=config["hidden_size"]
        self.device=config["device"]
        self.language=config["language"]
        self.beam_size=config["beam_size"]
        self.max_length=config["max_output_len"]
        self.device=config["device"]
        self.num_start=config["num_start"]
        #module
        """embedding_size, hidden_size,rnn_cell_type,bidirectional, num_layers=2, dropout_ratio=0.5"""
        self.embedder=BaiscEmbedder(config["vocab_size"],config["embedding_size"],config["dropout_ratio"])
        self.encoder=GraphBasedEncoder(config["embedding_size"],config["hidden_size"],config["rnn_cell_type"],\
                                        config["bidirectional"],config["num_layers"],config["dropout_ratio"])
        self.decoder=TreeDecoder(config["hidden_size"],config["operator_nums"],config["generate_size"],config["dropout_ratio"])
        self.node_generater=NodeGenerater(config["hidden_size"],config["operator_nums"],config["embedding_size"],config["dropout_ratio"])
        self.merge=SubTreeMerger(config["hidden_size"],config["embedding_size"],config["dropout_ratio"])
    
    def forward(self,seq, seq_length,group_nums,num_list, nums_stack, num_size, generate_nums, num_pos,\
                num_start,target=None, target_length=None,UNK_TOKEN=None):
        # sequence mask for attention
        seq_mask = []
        max_len = max(seq_length)
        for i in seq_length:
            seq_mask.append([0 for _ in range(i)] + [1 for _ in range(i, max_len)])
        seq_mask=torch.BoolTensor(seq_mask).to(self.device)

        num_mask = []
        max_num_size = max(num_size) + len(generate_nums)
        for i in num_size:
            d = i + len(generate_nums)
            num_mask.append([0] * d + [1] * (max_num_size - d))
        num_mask = torch.BoolTensor(num_mask).to(self.device)
        
        #build graph inputs
        #graphs=self.build_graph_input(seq_source,seq_length,num_list,num_pos)
        graphs=self.build_graph(seq_length,num_list,num_pos,group_nums)

        padding_hidden = torch.FloatTensor([0.0 for _ in range(self.hidden_size)]).unsqueeze(0).to(self.device)
        batch_size = len(seq_length)
        seq_emb=self.embedder(seq)
        pade_outputs, encoder_outputs = self.encoder(seq_emb, seq_length,graphs)
        problem_output = pade_outputs[:, -1, :self.hidden_size] +pade_outputs[:, 0, self.hidden_size:]
        #encoder_outputs = pade_outputs[:, :, :self.hidden_size] + pade_outputs[:, :, self.hidden_size:]

        if target != None:
            all_node_outputs=self.generate_node(encoder_outputs,problem_output,target,target_length,\
                                num_pos,nums_stack,padding_hidden,seq_mask,num_mask,UNK_TOKEN,num_start)
        else:
            all_node_outputs=self.generate_node_(encoder_outputs,problem_output,padding_hidden,seq_mask,num_mask,num_pos,num_start)
            return all_node_outputs
        # all_leafs = torch.stack(all_leafs, dim=1)  # B x S x 2
        all_node_outputs = torch.stack(all_node_outputs, dim=1).to(self.device)  # B x S x N
        return all_node_outputs
    
    def generate_node(self,encoder_outputs,problem_output,target,target_length,\
                        num_pos,nums_stack,padding_hidden,seq_mask,num_mask,unk,num_start):
        batch_size=encoder_outputs.size(0)
        # Prepare input and output variables
        node_stacks = [[TreeNode(_)] for _ in problem_output.split(1, dim=0)]

        max_target_length = max(target_length)

        all_node_outputs = []
        # all_leafs = []
        copy_num_len = [len(_) for _ in num_pos]
        num_size = max(copy_num_len)
        all_nums_encoder_outputs = self.get_all_number_encoder_outputs(
            encoder_outputs, num_pos, num_size,self.hidden_size)
        left_childs = [None for _ in range(batch_size)]
        embeddings_stacks = [[] for _ in range(batch_size)]
        for t in range(max_target_length):
            num_score, op, current_embeddings, current_context, current_nums_embeddings = self.decoder(
                node_stacks, left_childs, encoder_outputs,
                all_nums_encoder_outputs, padding_hidden, seq_mask, num_mask)
            # all_leafs.append(p_leaf)
            outputs = torch.cat((op, num_score), 1)
            all_node_outputs.append(outputs)

            target_t, generate_input = self.generate_tree_input(
                target[:,t].tolist(), outputs, nums_stack, num_start, unk)
            target[:,t] = target_t
            generate_input = generate_input.to(self.device)
            left_child, right_child, node_label = self.node_generater(
                current_embeddings, generate_input, current_context)
            left_childs = []
            for idx, l, r, node_stack, i, o in zip(range(batch_size),
                                                   left_child.split(1),
                                                   right_child.split(1),
                                                   node_stacks,
                                                   target[:,t].tolist(),
                                                   embeddings_stacks):
                if len(node_stack) != 0:
                    node = node_stack.pop()
                else:
                    left_childs.append(None)
                    continue

                if i < num_start:
                    node_stack.append(TreeNode(r))
                    node_stack.append(TreeNode(l, left_flag=True))
                    o.append(TreeEmbedding(node_label[idx].unsqueeze(0),
                                           False))
                else:
                    current_num = current_nums_embeddings[
                        idx, i - num_start].unsqueeze(0)
                    while len(o) > 0 and o[-1].terminal:
                        sub_stree = o.pop()
                        op = o.pop()
                        current_num = self.merge(op.embedding,
                                                 sub_stree.embedding,
                                                 current_num)
                    o.append(TreeEmbedding(current_num, True))
                if len(o) > 0 and o[-1].terminal:
                    left_childs.append(o[-1].embedding)
                else:
                    left_childs.append(None)
        return all_node_outputs
    
    def generate_node_(self,encoder_outputs,problem_output,padding_hidden,seq_mask,num_mask,num_pos,\
                        num_start):
        batch_size=encoder_outputs.size(0)
        # Prepare input and output variables
        node_stacks = [[TreeNode(_)] for _ in problem_output.split(1, dim=0)]
        
        num_size = len(num_pos[0])
        all_nums_encoder_outputs = self.get_all_number_encoder_outputs(
            encoder_outputs, num_pos, num_size,
            self.encoder.hidden_size)
        
        embeddings_stacks = [[] for _ in range(batch_size)]
        left_childs = [None for _ in range(batch_size)]
        beams = [
            TreeBeam(0.0, node_stacks, embeddings_stacks, left_childs, [])
        ]
        for t in range(self.max_length):
            current_beams = []
            while len(beams) > 0:
                b = beams.pop()
                if len(b.node_stack[0]) == 0:
                    current_beams.append(b)
                    continue
                # left_childs = torch.stack(b.left_childs)
                left_childs = b.left_childs

                num_score, op, current_embeddings, current_context, current_nums_embeddings = self.decoder(
                    b.node_stack, left_childs, encoder_outputs,
                    all_nums_encoder_outputs, padding_hidden, seq_mask,
                    num_mask)

                out_score = nn.functional.log_softmax(torch.cat(
                    (op, num_score), dim=1),
                                                      dim=1)

                # out_score = p_leaf * out_score

                topv, topi = out_score.topk(self.beam_size)

                for tv, ti in zip(topv.split(1, dim=1), topi.split(1, dim=1)):
                    current_node_stack = self.copy_list(b.node_stack)
                    current_left_childs = []
                    current_embeddings_stacks = self.copy_list(b.embedding_stack)
                    current_out = copy.deepcopy(b.out)

                    out_token = int(ti)
                    current_out.append(out_token)

                    node = current_node_stack[0].pop()

                    if out_token < num_start:
                        generate_input = torch.LongTensor([out_token
                                                           ]).to(self.device)
                        
                        left_child, right_child, node_label = self.node_generater(
                            current_embeddings, generate_input,
                            current_context)

                        current_node_stack[0].append(TreeNode(right_child))
                        current_node_stack[0].append(
                            TreeNode(left_child, left_flag=True))

                        current_embeddings_stacks[0].append(
                            TreeEmbedding(node_label[0].unsqueeze(0), False))
                    else:
                        current_num = current_nums_embeddings[
                            0, out_token - num_start].unsqueeze(0)

                        while len(
                                current_embeddings_stacks[0]
                        ) > 0 and current_embeddings_stacks[0][-1].terminal:
                            sub_stree = current_embeddings_stacks[0].pop()
                            op = current_embeddings_stacks[0].pop()
                            current_num = self.merge(op.embedding,
                                                     sub_stree.embedding,
                                                     current_num)
                        current_embeddings_stacks[0].append(
                            TreeEmbedding(current_num, True))
                    if len(current_embeddings_stacks[0]
                           ) > 0 and current_embeddings_stacks[0][-1].terminal:
                        current_left_childs.append(
                            current_embeddings_stacks[0][-1].embedding)
                    else:
                        current_left_childs.append(None)
                    current_beams.append(
                        TreeBeam(b.score + float(tv), current_node_stack,
                                 current_embeddings_stacks,
                                 current_left_childs, current_out))
            beams = sorted(current_beams, key=lambda x: x.score, reverse=True)
            beams = beams[:self.beam_size]
            flag = True
            for b in beams:
                if len(b.node_stack[0]) != 0:
                    flag = False
            if flag:
                break
        return beams[0].out
    
    def build_graph(self,seq_length,num_list,num_pos,group_nums):
        max_len=seq_length.max()
        batch_size=len(seq_length)
        batch_graph=[]
        for b_i in range(batch_size):
            x=torch.zeros((max_len,max_len))
            for idx in range(seq_length[b_i]):
                x[idx,idx]=1
            quantity_cell_graph=torch.clone(x)
            graph_greater=torch.clone(x)
            graph_lower=torch.clone(x)
            graph_quanbet=torch.clone(x)
            graph_attbet=torch.clone(x)
            for idx,n_pos in enumerate(num_pos[b_i]):
                for pos in group_nums[b_i][idx]:
                    quantity_cell_graph[n_pos,pos]=1
                    quantity_cell_graph[pos,n_pos]=1
                    graph_quanbet[n_pos,pos]=1
                    graph_quanbet[pos,n_pos]=1
                    graph_attbet[n_pos,pos]=1
                    graph_attbet[pos,n_pos]=1
            for idx_i in range(len(num_pos[b_i])):
                for idx_j in range(len(num_pos[b_i])):
                    if '%' in num_list[b_i][idx_i]:
                        num_i=eval(num_list[b_i][idx_i][:-1]+'/100')
                    elif '(' in num_list[b_i][idx_i]:
                        num_i=eval(num_list[b_i][idx_i][1:-1])
                    else:
                        num_i=float(num_list[b_i][idx_i])
                    if '%' in num_list[b_i][idx_j]:
                        num_j=eval(num_list[b_i][idx_j][:-1]+'/100')
                    elif '(' in num_list[b_i][idx_j]:
                        num_j=eval(num_list[b_i][idx_j][1:-1])
                    else:
                        num_j=float(num_list[b_i][idx_j])
                    if num_i > num_j:
                        graph_greater[num_pos[b_i][idx_i]][num_pos[b_i][idx_j]] = 1
                        graph_lower[num_pos[b_i][idx_j]][num_pos[b_i][idx_i]]=1
                    else:
                        graph_greater[num_pos[b_i][idx_j]][num_pos[b_i][idx_i]] = 1
                        graph_lower[num_pos[b_i][idx_i]][num_pos[b_i][idx_j]]=1
            group_num_=itertools.chain.from_iterable(group_nums[b_i])
            combn=itertools.permutations(group_num_,2)
            for idx in combn:
                graph_quanbet[idx]=1
                graph_quanbet[idx]=1
                graph_attbet[idx]=1
                graph_attbet[idx]=1
            quantity_cell_graph=quantity_cell_graph.to(self.device)
            graph_greater=graph_greater.to(self.device)
            graph_lower=graph_lower.to(self.device)
            graph_quanbet=graph_quanbet.to(self.device)
            graph_attbet=graph_attbet.to(self.device)
            graph=torch.stack([quantity_cell_graph,graph_greater,graph_lower,graph_quanbet,graph_attbet],dim=0)
            batch_graph.append(graph)
        batch_graph=torch.stack(batch_graph)
        return batch_graph
    
    def build_graph_input(self,sentences,sent_len,num_list,num_pos):
        max_len=sent_len.max()
        nlp=stanza.Pipeline(self.language,processors='depparse,tokenize,pos,lemma',tokenize_pretokenized=True,logging_level='error')
        
        group_nums=[]
        graphs=[]
        for b_i,sentence in enumerate(sentences):
            doc=nlp(sentence)
            token_list=doc.to_dict()[0]
            group_num,quantity_cell_graph,graph_greater,graph_lower,graph_quanbet,graph_attbet\
                =self.get_group_num(token_list,0,num_pos[b_i],num_list[b_i],max_len,sent_len[b_i])
            quantity_cell_graph=quantity_cell_graph.to(self.device)
            graph_greater=graph_greater.to(self.device)
            graph_lower=graph_lower.to(self.device)
            graph_quanbet=graph_quanbet.to(self.device)
            graph_attbet=graph_attbet.to(self.device)
            graph=torch.stack([quantity_cell_graph,graph_greater,graph_lower,graph_quanbet,graph_attbet],dim=0)
            graphs.append(graph)
            group_nums.append(group_num)
        graphs=torch.stack(graphs)
        return graphs
    
    def get_group_num(self,token_list,sent_slide,num_pos,num_list,max_len,sent_len):
        x=torch.zeros((max_len,max_len))
        for idx in range(sent_len):
            x[idx,idx]=1
        quantity_cell_graph=torch.clone(x)
        graph_greater=torch.clone(x)
        graph_lower=torch.clone(x)
        graph_quanbet=torch.clone(x)
        graph_attbet=torch.clone(x)
        group_nums=[]
        num_pos=[pos-sent_slide for pos in num_pos]
        for n_pos in num_pos:
            pos_stack=[]
            group_num=[]
            pos_stack.append([n_pos,token_list[n_pos]["deprel"]])
            head_pos=token_list[n_pos]['head']
            for idx,x in enumerate(token_list):
                if x['head']==head_pos and n_pos!=idx:
                    deprel=x["deprel"]
                    pos_stack.append([idx,deprel])
            while pos_stack:
                pos_dep=pos_stack.pop(0)
                pos=pos_dep[0]
                dep=pos_dep[1]
                head_pos=token_list[pos]['head']-1
                upos=token_list[pos]['upos']
                if upos not in ['NOUN','NUM','ADJ','VERB','DET', 'SYM']:
                    continue
                elif upos == 'NOUN' and dep not in ['compound','nsubj:pass','nsubj','compound']:
                    continue
                elif upos == 'VERB' and dep not in ['conj','root']:
                    continue
                elif upos == 'ADJ' and dep not in ['amod']:
                    continue
                elif upos == 'DET' and dep not in ['advmod']:
                    continue
                elif upos == 'SYM' and dep not in ['obl']:
                    continue
                else:
                    group_num.append(pos+sent_slide)
                if head_pos>=0:
                    head_dep=token_list[head_pos]['deprel']
                    if [head_pos,head_dep] in pos_stack:
                        pass
                    else:
                        pos_stack.append([head_pos,head_dep])
            if group_num == []:
                group_num.append(n_pos)
            if len(group_num) == 1:
                if n_pos-1 >=0:
                    group_num.append(n_pos-1)
                if n_pos+1 <= sent_len:
                    group_num.append(n_pos+1)
            for pos in group_num:
                quantity_cell_graph[n_pos,pos]=1
                quantity_cell_graph[pos,n_pos]=1
                graph_quanbet[n_pos,pos]=1
                graph_quanbet[pos,n_pos]=1
                graph_attbet[n_pos,pos]=1
                graph_attbet[pos,n_pos]=1
            group_nums.append(group_num)
        for idx_i in range(len(num_pos)):
            for idx_j in range(len(num_pos)):
                try:
                    num_i=eval(num_list[idx_i])
                except:# % in num
                    num_i=eval(num_list[idx_i][:-1]+'/100')
                try:
                    num_j=eval(num_list[idx_j])
                except:
                    num_j=eval(num_list[idx_j][:-1]+'/100')
                if num_i > num_j:
                    graph_greater[num_pos[idx_i]][num_pos[idx_j]] = 1
                    graph_lower[num_pos[idx_j]][num_pos[idx_i]]=1
                else:
                    graph_greater[num_pos[idx_j]][num_pos[idx_i]] = 1
                    graph_lower[num_pos[idx_i]][num_pos[idx_j]]=1
        group_nums_=itertools.chain.from_iterable(group_nums)
        combn=itertools.permutations(group_nums_,2)
        for idx in combn:
            graph_quanbet[idx]=1
            graph_quanbet[idx]=1
            graph_attbet[idx]=1
            graph_attbet[idx]=1
        # for idx_i in group_nums:
        #     for idx_j in group_nums:
                # graph_quanbet[idx_i,idx_j]=1
                # graph_quanbet[idx_j,idx_i]=1
                # graph_attbet[idx_i,idx_j]=1
                # graph_attbet[idx_j,idx_i]=1
        return group_nums,quantity_cell_graph,graph_greater,graph_lower,graph_quanbet,graph_attbet

    def get_all_number_encoder_outputs(self,encoder_outputs, num_pos, num_size, hidden_size):
        indices = list()
        sen_len = encoder_outputs.size(1)
        batch_size=encoder_outputs.size(0)
        masked_index = []
        temp_1 = [1 for _ in range(hidden_size)]
        temp_0 = [0 for _ in range(hidden_size)]
        for b in range(batch_size):
            for i in num_pos[b]:
                indices.append(i + b * sen_len)
                masked_index.append(temp_0)
            indices += [0 for _ in range(len(num_pos[b]), num_size)]
            masked_index += [temp_1 for _ in range(len(num_pos[b]), num_size)]
        indices = torch.LongTensor(indices).to(self.device)
        masked_index = torch.BoolTensor(masked_index).to(self.device)
        
        masked_index = masked_index.view(batch_size, num_size, hidden_size)
        all_outputs = encoder_outputs.contiguous()
        all_embedding = all_outputs.view(-1, encoder_outputs.size(2))  # S x B x H -> (B x S) x H
        all_num = all_embedding.index_select(0, indices)
        all_num = all_num.view(batch_size, num_size, hidden_size)
        return all_num.masked_fill_(masked_index, 0.0)
    
    def generate_tree_input(self,target, decoder_output, nums_stack_batch, num_start, unk):
        # when the decoder input is copied num but the num has two pos, chose the max
        target_input = copy.deepcopy(target)
        for i in range(len(target)):
            if target[i] == unk:
                num_stack = nums_stack_batch[i].pop()
                max_score = -float("1e12")
                for num in num_stack:
                    if decoder_output[i, num_start + num] > max_score:
                        target[i] = num + num_start
                        max_score = decoder_output[i, num_start + num]
            if target_input[i] >= num_start:
                target_input[i] = 0
        return torch.LongTensor(target), torch.LongTensor(target_input)
    
    def copy_list(self,l):
        r = []
        if len(l) == 0:
            return r
        for i in l:
            if type(i) is list:
                r.append(self.copy_list(i))
            else:
                r.append(i)
        return r
    
    def __str__(self) -> str:
        info=super().__str__()
        total=sum(p.numel() for p in self.parameters())
        trainable=sum(p.numel() for p in self.parameters() if p.requires_grad)
        parameters="\ntotal parameters : {} \ntrainable parameters : {}".format(total,trainable)
        return info+parameters

