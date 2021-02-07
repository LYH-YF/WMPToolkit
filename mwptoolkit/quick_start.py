import importlib

from logging import getLogger
from mwptoolkit.utils.enum_type import TaskType

from mwptoolkit.config.configuration import Config
from mwptoolkit.data.utils import *
from mwptoolkit.evaluate.evaluator import Evaluater, SeqEvaluater,PostEvaluater
from mwptoolkit.utils.utils import get_model, init_seed
def get_trainer(task_type, model_name):
    r"""Automatically select trainer class based on model type and model name

    Args:
        model_type (~mwptoolkit.utils.enum_type.TaskType): model type
        model_name (str): model name

    Returns:
        ~mwptoolkit.trainer.trainer.Trainer: trainer class
    """
    try:
        return getattr(importlib.import_module('mwptoolkit.trainer.trainer'), model_name + 'Trainer')
    except AttributeError:
        if task_type == TaskType.SingleEquation:
            return getattr(importlib.import_module('mwptoolkit.trainer.trainer'), 'SingleEquationTrainer')
        else:
            return getattr(importlib.import_module('mwptoolkit.trainer'), 'Trainer')

def run_toolkit():
    config=Config()

    init_seed(config['random_seed'], True)

    logger = getLogger()

    logger.info(config)

    dataset=create_dataset(config)

    if config["share_vocab"]:
        config["out_symbol2idx"]=dataset.out_symbol2idx
        config["out_idx2symbol"]=dataset.out_idx2symbol
        config["in_word2idx"]=dataset.in_word2idx
        config["in_idx2word"]=dataset.in_idx2word
        config["out_sos_token"]=dataset.in_word2idx["<SOS>"]
    else:
        if config["symbol_for_tree"]:
            pass
        else:
            config["out_sos_token"]=dataset.out_symbol2idx["<SOS>"]
    
    config["vocab_size"]=len(dataset.in_idx2word)
    config["symbol_size"]=len(dataset.out_idx2symbol)
    config["operator_nums"]=dataset.operator_nums
    config["copy_nums"]=dataset.copy_nums
    config["generate_size"]=len(dataset.generate_list)
    
    dataloader=create_dataloader(config)(config,dataset)
    if config["equation_fix"] == "prefix":
        evaluator=Evaluater(dataset.out_symbol2idx,dataset.out_idx2symbol,config)
    elif config["equation_fix"] ==None:
        evaluator=SeqEvaluater(dataset.out_symbol2idx,dataset.out_idx2symbol,config)
    elif config["equation_fix"]== "postfix":
        evaluator=PostEvaluater(dataset.out_symbol2idx,dataset.out_idx2symbol,config)
    else:
        raise NotImplementedError

    model=get_model(config["model"])(config).to(config["device"])

    trainer=get_trainer(config["task_type"],config["model"])(config, model, dataloader, evaluator)

    trainer.fit()


