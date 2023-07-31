import sys

class GoalLabeller:

    def __init__(self):
        self.next_label = 1
        self.next_comm = 1
        self.op_dict = {}
        self.comm_dict = {}

    def GetLabel(self, op):
        if op in self.op_dict:
            pass
        else:
            self.op_dict[op] = self.next_label
            self.next_label += 1
        return self.op_dict[op]

    def GetCommID(self, comm):
        if comm in self.comm_dict:
            pass
        else:
            self.comm_dict[comm] = self.next_comm
            self.next_comm += 1
        return self.comm_dict[comm]

    def MakeTag(self, tag, comm):
        """ Combine the user tag and the comm tag portion """
        return tag * 1000 + comm

class GoalOp:

    def __init__(self):
        self.depends_on = []

    def requires(self, required):
        # TODO check that self and required translate to the same rank in comm_world - we don't have the rank here :(
        self.depends_on.append(required)


class GoalSend(GoalOp):

    def __init__(self, dst, tag, size):
        super().__init__()
        self.dst = dst
        self.tag = tag
        self.size = size

    def write_goal(self, labeller, fh, comm, basecomm):
        fh.write("l{label}: send {size}b to {dst} tag {tag}\n".format(label=labeller.GetLabel(self), 
                                                                     size=str(self.size), 
                                                                     dst=str(comm.TranslateRank(self.dst, basecomm)),
                                                                     tag=str(labeller.MakeTag(self.tag, labeller.GetCommID(comm)))))

class GoalRecv(GoalOp):

    def __init__(self, src, tag, size):
        super().__init__()
        self.src = src
        self.tag = tag
        self.size = size

    def write_goal(self, labeller, fh, comm, basecomm):
        fh.write("l{label}: recv {size}b from {src} tag {tag}\n".format(label=labeller.GetLabel(self), 
                                                                       size=str(self.size), 
                                                                       src=str(comm.TranslateRank(self.src, basecomm)),
                                                                       tag=str(labeller.MakeTag(self.tag, labeller.GetCommID(comm)))))

class GoalCalc(GoalOp):

    def __init__(self, size):
        super().__init__()
        self.size = size

    def write_goal(self, labeller, fh, comm, basecomm):
        fh.write("l{label}: calc {size}\n".format(label=labeller.GetLabel(self), size=str(self.size)))



class GoalRank:

    def __init__(self, comm, rank):
        self.comm = comm
        self.rank = rank
        self.base_rank = None
        self.ops = []

    def Send(self, dst, tag, size):
        if dst > self.comm.CommSize():
            raise ValueError(str(dst)+" is larger than comm size!")
        op = GoalSend(dst=dst, tag=tag, size=size)
        self.ops.append(op)
        return op

    def Recv(self, src, tag, size):
        if src > self.comm.CommSize():
            raise ValueError(str(src)+" is larger than comm size!")
        op = GoalRecv(src=src, tag=tag, size=size)
        self.ops.append(op)
        return op

    def Calc(self, size):
        op = GoalCalc(size=size)
        self.ops.append(op)
        return op



    def write_goal(self, labeller, fh, rankid=True, basecomm=None):
        if basecomm is None:
            basecomm = self.comm # stupid python evals default args at method definition, not call time :(
        if rankid:
            fh.write("rank "+str(self.rank)+" {\n")
        for op in self.ops:
            op.write_goal(labeller, fh, self.comm, basecomm)
        for op in self.ops:
            for req in op.depends_on:
                fh.write("l{label1} requires l{label2}\n".format(label1=labeller.GetLabel(op), label2=labeller.GetLabel(req)))
        for sc in self.comm.subcomms:
            sc.write_goal_subcomm(labeller, fh, self.rank, basecomm)
        if rankid:
            fh.write("}\n\n")


class GoalComm:

    def __init__(self, comm_size):
        self.base_comm = self
        self.comm_size = comm_size
        self.subcomms = []
        self.ranks = [GoalRank(comm=self, rank=rank) for rank in range(comm_size)]

    def __getitem__(self, index):
        return self.ranks[index]

    def Send(self, src, dst, tag, size):
        return self[src].Send(dst, tag, size)

    def Recv(self, dst, src, tag, size):
        return self[dst].Recv(src, tag, size)

    def Calc(self, host, size):
        return self[host].Calc(size)

    def CommSize(self):
        return self.comm_size

    def CommSplit(self, color, key):
        if len(list(color)) < self.comm_size or len(list(key)) < self.comm_size:
            raise ValueError("The length of color and key array must match the communicator size.")
        newcomms = []
        order = [ (oldrank, color[oldrank], key[oldrank]) for oldrank in range(0, self.comm_size) ]
        color_buckets = {}
        for o in order:
            if o[1] in color_buckets:
                color_buckets[o[1]].append(o)
            else:
                color_buckets[o[1]]=[o]
        for c in color_buckets.keys():
            c_list = sorted(color_buckets[c], key=lambda x: x[2]) # sort by key within color
            nc = GoalComm(len(c_list))
            nc.base_comm = self
            for idx, r in enumerate(nc):
                r.base_rank = c_list[idx][0] # store the rank the new rank had in the comm it was splitted from
            newcomms.append(nc)
        self.subcomms += newcomms
        return newcomms

    def write_goal(self, labeller=None, fh=sys.stdout):
        fh.write("num_ranks "+str(len(self.ranks))+"\n\n")
        if labeller is None:
            labeller = GoalLabeller()
        for r in self.ranks:
            r.write_goal(labeller, fh, rankid=True, basecomm=self)

    def write_goal_subcomm(self, labeller, fh, rank, basecomm):
        """ if this comm has a rank with base_rank=rank, print its goal ops without enclosing brackets """
        for r in self.ranks:
            if r.base_rank == rank:
                r.write_goal(labeller, fh, rankid=False, basecomm=basecomm)

    def TranslateRank(self, rank, basecomm):
        """ Find out the rank id of the given rank (in self) in basecomm """
        if self == basecomm:
            return rank
        if rank == None:
            raise ValueError("Attempt to translate a non-existing rank!")
        return self.base_comm.TranslateRank(self.ranks[rank].base_rank, basecomm)

if __name__ == "__main__":
    
    comm_world = GoalComm(4)
    comms = comm_world.CommSplit(color=[0,0,1,1], key=[0,1,2,3])
    comms[0][0].Send(1, 42, 32)
    comms[0][1].Recv(0, 42, 32)
    comms[1][1].Send(0, 42, 16)
    comms[1][0].Recv(1, 42, 16)
    comms[1][0].Calc(23)
    comms[0].write_goal()
    comms[1].write_goal()
    comm_world.write_goal()

