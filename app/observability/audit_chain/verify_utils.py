from typing import List, Optional, Tuple


def verify_chain_integrity(logs: List) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    prev_hash = None
    for i, log in enumerate(logs):
        if i == 0:
            if log.hash_prev is not None:
                return False, log.log_id, None, log.hash_prev
            prev_hash = log.hash_self
            continue
        if log.hash_prev != prev_hash:
            return False, log.log_id, prev_hash, log.hash_prev
        prev_hash = log.hash_self
    return True, None, None, None
