import os, torch, argparse, math
import numpy as np
from copy import deepcopy
from tqdm import tqdm, trange

from vq import VectorQuantize
from utils import read_ply_data, write_ply_data, load_vqdvgo


def parse_args():
    parser = argparse.ArgumentParser(description="codebook based quantization")
    parser.add_argument("--important_score_npz_path", type=str, default='room')
    parser.add_argument("--input_path", type=str, default='room/iteration_40000/point_cloud.ply')
    
    parser.add_argument("--save_path", type=str, default='./output/room')  
    parser.add_argument("--importance_prune", type=float, default=1.0)
    parser.add_argument("--importance_include", type=float, default=0.0)
    parser.add_argument("--no_load_data", type=bool, default=False)
    parser.add_argument("--no_save_ply", type=bool, default=False)
    parser.add_argument("--sh_degree", type=int, default=2)
    parser.add_argument("--ablation", type=int, default=5) 

    parser.add_argument("--iteration_num", type=float, default=1000)
    parser.add_argument("--vq_ratio", type=float, default=0.6)
    parser.add_argument("--codebook_size", type=int, default=2**13)  # 2**13 = 8192
    parser.add_argument("--no_IS", type=bool, default=False)
    parser.add_argument("--vq_way", type=str, default='half') # wage

    opt = parser.parse_args() 
    return opt
    

class Quantization():
    def __init__(self, opt):
        
        # ----- load ply data -----
        if opt.sh_degree == 3:
            self.sh_dim = 3+45

        elif opt.sh_degree == 2:

            if opt.ablation == 7:
                self.sh_dim = 3+24+8
            else:
                self.sh_dim = 3+24

        self.feats = read_ply_data(opt.input_path)
        self.feats = torch.tensor(self.feats)
        self.feats_bak = self.feats.clone()
        self.feats = self.feats[:, 6:6+self.sh_dim]

        # ----- define model -----
        self.model_vq = VectorQuantize(
                    dim = self.feats.shape[1],              
                    codebook_size = opt.codebook_size,
                    decay = 0.8,                            # specify number of quantizersse， 对应公式(9)的 λ_d
                    commitment_weight = 1.0,                # codebook size
                    use_cosine_sim = False,
                    threshold_ema_dead_code=0,
                ).to(device)
        
        # ----- other -----
        self.save_path = opt.save_path
        self.ply_path = opt.save_path
        self.imp_path = opt.important_score_npz_path
        self.high = None
        self.VQ_CHUNK = 80000
        self.k_expire = 10        
        self.vq_ratio = opt.vq_ratio

        self.no_IS = opt.no_IS
        self.no_load_data = opt.no_load_data
        self.no_save_ply = opt.no_save_ply
   
        self.codebook_size = opt.codebook_size
        self.importance_prune = opt.importance_prune
        self.importance_include = opt.importance_include
        self.iteration_num = opt.iteration_num

        self.vq_way = opt.vq_way
        self.ablation = opt.ablation

        if self.ablation == 5:
            self.codebook_size = 4096*2
        

        # ----- Ablation -----
        # 1. baseline (prune + distill过的模型)
        # 2. baseline + fp16， size掉一半
        # 3. baseline + codebook (4096)
        # 4. baseline + codebook (4096x2)
        # 5. baseline + codebook (4096x4)
        # 6. baseline + vectree 

        # ----- print info -----
        print("=========================================")
        print("input_feats_shape: ", self.feats_bak.shape)
        print("vq_feats_shape: ", self.feats.shape)
        print("SH_degree: ", opt.sh_degree)
        print("Quantization_ratio: ", opt.vq_ratio)
        print("Add_important_score: ", opt.no_IS==False)
        print("Codebook_size: ", opt.codebook_size)
        print("=========================================")

    @torch.no_grad()
    def calc_vector_quantized_feature(self):
        """
        apply vector quantize on feature grid and return vq indexes
        """
        print("caculate vq features")
        CHUNK = 8192
        feat_list = []
        indice_list = []
        self.model_vq.eval()
        self.model_vq._codebook.embed.half().float()   #
        for i in tqdm(range(0, self.feats.shape[0], CHUNK)):
            feat, indices, commit = self.model_vq(self.feats[i:i+CHUNK,:].unsqueeze(0).to(device))
            indice_list.append(indices[0])
            feat_list.append(feat[0])
        self.model_vq.train()
        all_feat = torch.cat(feat_list).half().float()  # [num_elements, feats_dim]
        all_indice = torch.cat(indice_list)             # [num_elements, 1]
        return all_feat, all_indice


    @torch.no_grad()
    def fully_vq_reformat(self):  
        print("start vector quantize")
        all_feat, all_indice = self.calc_vector_quantized_feature()

        if self.save_path is not None:
            save_path = self.save_path
            os.makedirs(f'{save_path}/extreme_saving', exist_ok=True)

            # ----- save basic info -----
            metadata = dict()
            metadata['input_pc_num'] = self.feats_bak.shape[0]  
            metadata['input_pc_dim'] = self.feats_bak.shape[1]  
            metadata['codebook_size'] = self.codebook_size
            metadata['codebook_dim'] = self.sh_dim
            np.savez_compressed(f'{save_path}/extreme_saving/metadata.npz', metadata=metadata)

            # ===================================================== save vq_SH =============================================
            # ----- save mapping_index (vq_index) -----
            def dec2bin(x, bits):
                mask = 2 ** torch.arange(bits - 1, -1, -1).to(x.device, x.dtype)
                return x.unsqueeze(-1).bitwise_and(mask).ne(0).float()    
            # vq indice was saved in according to the bit length
            self.codebook_vq_index = all_indice[torch.logical_xor(self.all_one_mask,self.non_vq_mask)]                             # vq_index
            bin_indices = dec2bin(self.codebook_vq_index, int(math.log2(self.codebook_size))).bool().cpu().numpy()                 # mapping_index
            np.savez_compressed(f'{save_path}/extreme_saving/vq_indexs.npz',np.packbits(bin_indices.reshape(-1)))               
            
            # ----- save codebook -----                                           
            codebook = self.model_vq._codebook.embed.cpu().half().numpy().squeeze(0)                                                 
            np.savez_compressed(f'{save_path}/extreme_saving/codebook.npz', codebook)

            # ----- save keep mask (non_vq_feats_index)-----
            np.savez_compressed(f'{save_path}/extreme_saving/non_vq_mask.npz',np.packbits(self.non_vq_mask.reshape(-1).cpu().numpy()))

            # ===================================================== save non_vq_SH =============================================
            non_vq_feats = self.feats_bak[self.non_vq_mask, 6:6+self.sh_dim]       
            wage_non_vq_feats = self.wage_vq(non_vq_feats)
            np.savez_compressed(f'{save_path}/extreme_saving/non_vq_feats.npz', wage_non_vq_feats) 

            # =========================================== save xyz &f other attr(opacity + 3*scale + 4*rot) ====================================
            other_attribute = self.feats_bak[:, -8:]
            wage_other_attribute = self.wage_vq(other_attribute)
            np.savez_compressed(f'{save_path}/extreme_saving/other_attribute.npz', wage_other_attribute)

            xyz = self.feats_bak[:, 0:3]
            np.savez_compressed(f'{save_path}/extreme_saving/xyz.npz', xyz)  # octreed based compression will be updated 
            

        # zip everything together to get final size
        os.system(f"zip -r {save_path}/extreme_saving.zip {save_path}/extreme_saving")
        size = os.path.getsize(f'{save_path}/extreme_saving.zip')
        size_MB = size / 1024.0 / 1024.0
        print("size = ", size_MB, " MB")
            
        print("finish vector quantize!")
        return all_feat, all_indice
    
    def load_f(self, path, name, allow_pickle=False,array_name='arr_0'):
        return np.load(os.path.join(path, name),allow_pickle=allow_pickle)[array_name]

    def wage_vq(self, feats):
        if self.vq_way == 'half':        
            return feats.half()
        else:
            return feats
    
    def quantize(self):
        if self.no_IS:                                                      #  no important score
            importance = np.ones((self.feats.shape[0]))                     
        else:
            importance = self.load_f(self.imp_path, 'imp_score.npz')

        ###################################################
        only_vq_some_vector = True
        if only_vq_some_vector:
            tensor_importance = torch.tensor(importance)
            large_val, large_index = torch.topk(tensor_importance, k=int(tensor_importance.shape[0] * (1-self.vq_ratio)), largest=True) 
            self.all_one_mask = torch.ones_like(tensor_importance).bool()     
            self.non_vq_mask = torch.zeros_like(tensor_importance).bool()         
            self.non_vq_mask[large_index] = True                         
        self.non_vq_index = large_index

        IS_non_vq_point = large_val.sum()
        IS_all_point = tensor_importance.sum()
        IS_percent = IS_non_vq_point/IS_all_point
        print("IS_percent: ", IS_percent)

        #=================== Codebook initialization ====================
        self.model_vq.train()
        with torch.no_grad():
            self.vq_mask = torch.logical_xor(self.all_one_mask, self.non_vq_mask)                  
            feats_needs_vq = self.feats[self.vq_mask].clone()                                       
            imp = tensor_importance[self.vq_mask].float()                                        
            k = self.k_expire                                                              
            if k > self.model_vq.codebook_size:
                k = 0            
            for i in trange(self.iteration_num):
                indexes = torch.randint(low=0, high=feats_needs_vq.shape[0], size=[self.VQ_CHUNK])         
                vq_weight = imp[indexes].to(device)
                vq_feature = feats_needs_vq[indexes,:].to(device)
                quantize, embed, loss = self.model_vq(vq_feature.unsqueeze(0), weight=vq_weight.reshape(1,-1,1))

                replace_val, replace_index = torch.topk(self.model_vq._codebook.cluster_size, k=k, largest=False)      
                _, most_important_index = torch.topk(vq_weight, k=k, largest=True)
                self.model_vq._codebook.embed[:,replace_index,:] = vq_feature[most_important_index,:]

        #=================== Apply vector quantization ====================
        all_feat, all_indices = self.fully_vq_reformat()
        print('\n')
        print('\n')
        print('\n')
        print("output_feats: ", all_feat.shape)        
        print("quantized succcessfully!")



    def dequantize(self):
        print("Load saved data:")
        dequantized_feats = load_vqdvgo(os.path.join(self.save_path,'extreme_saving'), device=device)

        if self.no_save_ply == False:
            os.makedirs(f'{self.ply_path}/', exist_ok=True)
            write_ply_data(dequantized_feats.cpu().numpy(), self.ply_path, self.sh_dim)

        print("dequantized_feats: ", dequantized_feats.shape)
        print("dequantized succcessfully!")



if __name__=='__main__':
    opt = parse_args()
    device = torch.device('cuda')
    vq = Quantization(opt)

    vq.quantize()
    vq.dequantize()
    
    print("all done!!!")
