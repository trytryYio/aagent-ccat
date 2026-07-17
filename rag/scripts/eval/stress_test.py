import time
import os
import csv
import random
import logging
import concurrent.futures
from typing import List
from rag.hybrid_search import hybrid_search
from rag.embedding import embed_text, embed_image

# 配置日志
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# 配置路径（自动检测项目根目录）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE_DIR, "rag", "data")
PRODUCTS_CSV = os.path.join(DATA_DIR, "products.csv")
IMAGES_DIR = os.path.join(DATA_DIR, "images")

class RAGStressTester:
    def __init__(self):
        self.all_data = []
        self._load_data()

    def _load_data(self):
        if not os.path.exists(PRODUCTS_CSV):
            return
        with open(PRODUCTS_CSV, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            self.all_data = list(reader)

    def single_request(self):
        """模拟一次完整的混合搜索请求"""
        try:
            case = random.choice(self.all_data)
            img_path = os.path.join(IMAGES_DIR, f"{case['product_id']}.jpg")
            
            start_time = time.time()
            
            # 1. 模型推理
            with open(img_path, "rb") as f:
                img_emb = embed_image(f.read())
            text_emb = embed_text(case["name"])
            
            # 2. 向量检索
            results = hybrid_search(img_emb, text_emb, top_k=5)
            
            duration = (time.time() - start_time) * 1000
            return True, duration
        except Exception as e:
            logger.error(f"请求失败: {e}")
            return False, 0

    def run_benchmark(self, concurrent_users: int, total_requests: int):
        logger.info(f"\n>>> 压测开始: 并发用户数={concurrent_users}, 总请求数={total_requests}")
        
        latencies = []
        success_count = 0
        
        start_wall_time = time.time()
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrent_users) as executor:
            futures = [executor.submit(self.single_request) for _ in range(total_requests)]
            for future in concurrent.futures.as_completed(futures):
                success, duration = future.result()
                if success:
                    success_count += 1
                    latencies.append(duration)
        
        total_wall_time = time.time() - start_wall_time
        
        if latencies:
            latencies.sort()
            p50 = latencies[int(len(latencies) * 0.5)]
            p90 = latencies[int(len(latencies) * 0.9)]
            p95 = latencies[int(len(latencies) * 0.95)]
            avg = sum(latencies) / len(latencies)
            tps = success_count / total_wall_time
            
            logger.info(f"--- 压测结果 (并发={concurrent_users}) ---")
            logger.info(f"成功率: {success_count/total_requests:.1%}")
            logger.info(f"吞吐量 (TPS): {tps:.2f} req/s")
            logger.info(f"平均耗时: {avg:.1f}ms")
            logger.info(f"P50 耗时: {p50:.1f}ms")
            logger.info(f"P90 耗时: {p90:.1f}ms")
            logger.info(f"P95 耗时: {p95:.1f}ms")
            
            return p95
        return 9999

if __name__ == "__main__":
    tester = RAGStressTester()
    # 模拟从低到高的负载
    tester.run_benchmark(concurrent_users=1, total_requests=10)
    tester.run_benchmark(concurrent_users=3, total_requests=15)
    tester.run_benchmark(concurrent_users=5, total_requests=20)
