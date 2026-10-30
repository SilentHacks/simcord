Payload detachment uses exact type identity for its wire fast path. Custom metaclass equality can no longer bypass independent copying or the original whole-graph deepcopy hooks and failures.
