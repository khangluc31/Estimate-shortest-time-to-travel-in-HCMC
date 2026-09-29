import Data_preprocessing as dp
import heapq
import math
def heuristic(cur_node, tar_node, graph):
    u=graph.vertices[cur_node]
    v=graph.vertices[tar_node]
    dis=dp.haversine_m(u.y, u.x, v.y, v.x)
    vm=graph.v_max if graph.v_max >0 else dp.DEFAULT_SPEED
    return dis/(vm/3.6)
def bidirection_Astar(graph, start, goal):
    if goal==start:
        return [start], 0.0
    pq_f=[]
    pq_b=[]
    count=0
    h_s=heuristic(start,goal,graph)
    h_g=heuristic(goal,start,graph)
    heapq.heappush(pq_f,(h_s,count,start))
    count+=1
    heapq.heappush(pq_b,(h_g,count,goal))
    close_f=set()
    close_b=set()
    g_f={start: 0.0}
    g_b={goal: 0.0}
    par_f={start: None}
    par_b={goal: None}
    best=math.inf
    meet=None
    while pq_f and pq_b:
        if pq_f[0][0] >= best and pq_b[0][0] >= best:
            break
        if pq_f[0][0] <= pq_b[0][0]:
            f_cur,_, u=heapq.heappop(pq_f)
            if u in close_f:
                continue
            close_f.add(u)
            for e in graph.neighbors(u):
                if e.blocked or math.isinf(e.travel_time):
                    continue
                v=e.target
                cur_g=g_f[u]+e.travel_time
                if cur_g < g_f.get(v,math.inf):
                    g_f[v]=cur_g
                    par_f[v]=u
                    count +=1
                    f=cur_g+heuristic(v,goal,graph)
                    heapq.heappush(pq_f,(f,count,v))
                    if v in g_b:
                        cost=cur_g+g_b[v]
                        if cost <best:
                            best=cost
                            meet=v
        else:
            f_cur,_,u=heapq.heappop(pq_b)
            if u in close_b:
                continue
            close_b.add(u)
            for e in graph.predecessors(u):
                if e.blocked or math.isinf(e.travel_time):
                    continue
                v=e.source
                cur_g=g_b[u]+e.travel_time
                if cur_g < g_b.get(v, math.inf):
                    g_b[v]=cur_g
                    par_b[v]=u
                    count+=1
                    f=cur_g+heuristic(v,start,graph)
                    heapq.heappush(pq_b,(f,count,v))
                    if v in g_f:
                        cost=g_f[v]+cur_g
                        if cost < best:
                            best=cost
                            meet=v
    if meet is None:
        return None, math.inf
    path_f=[]
    cur=meet
    while cur is not None:
        path_f.append(cur)
        cur=par_f[cur]
    path_f.reverse()
    path_b=[]
    cur=par_b[meet]
    while cur is not None:
        path_b.append(cur)
        cur=par_b[cur]
    return path_f+path_b, best



