import requests
import json

# 测试李宁官网搜索 API 的可用性及其返回的数据结构
def test_api():
    url = "https://api.store.lining.com/goodsg/v1/goods-jh-query/search/lining/list/page"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://store.lining.com/goods/list?key=%E7%BE%BD%E6%AF%9B%E7%90%83%E9%9E%8B&searchSource=manual_search",
        "Origin": "https://store.lining.com"
    }
    
    payload = {
        "goodsJhSearchVO": {
            "key": "羽毛球鞋",
            "page": 1,
            "pageSize": 10
        }
    }
    
    print(f"Testing API with payload: {payload}")
    response = requests.post(url, headers=headers, data=json.dumps(payload))
    print(f"Status Code: {response.status_code}")
    print(f"Response: {response.text[:500]}")

if __name__ == "__main__":
    test_api()
