
def greedy_search(logits):
    r"""Find the index of max logits

    Args:
        logits (torch.Tensor): logits distribution

    Return:
        torch.Tensor: the chosen index of token
    """
    return logits.squeeze(1).argmax(dim=-1)