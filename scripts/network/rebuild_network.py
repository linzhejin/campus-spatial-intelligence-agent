"""强制重新下载并缓存扩展后的校园路网（覆盖信息学部）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from config import WHU_BBOX
from spatial import network

cache = os.path.join(ROOT, "data", "whu_road_network.graphml")
if os.path.exists(cache):
    os.remove(cache)
    print(f"已删除旧缓存: {cache}")

print(f"使用 bbox: {WHU_BBOX}")
G = network.reload_network(WHU_BBOX)
print(f"路网节点: {G.number_of_nodes()}, 边: {G.number_of_edges()}")
