#!/usr/bin/env bash
# wsl-port-proxy-reset.sh
#
# 保活 mysql-dev / redis-dev 容器，并打印当前 WSL eth0 IP。
# 由 Windows 端 scripts/wsl-port-proxy.ps1 调用，不需要手动跑。
#
# 退出码:
#  0 = 成功
#  1 = Docker 不可用
#  2 = 容器缺失（需用户手动 docker run）

set -u

CONTAINERS=("mysql-dev" "redis-dev")

# 检查 docker 是否可用
if ! command -v docker >/dev/null 2>&1; then
    echo "[ERR] docker 命令不存在" >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "[ERR] docker daemon 不可用" >&2
    exit 1
fi

# 逐个保活
missing=()
for c in "${CONTAINERS[@]}"; do
    state=$(docker inspect -f '{{.State.Status}}' "$c" 2>/dev/null || echo "missing")
    case "$state" in
        running)
            echo "[OK] $c 已在运行"
            ;;
        exited|created|paused|restarting)
            echo "[..] $c 状态=$state, 启动中..."
            docker start "$c" >/dev/null
            ;;
        missing)
            echo "[MISS] $c 不存在"
            missing+=("$c")
            ;;
        *)
            echo "[WARN] $c 未知状态: $state, 尝试启动..."
            docker start "$c" >/dev/null 2>&1
            ;;
    esac
done

# 等 2s 让容器绑端口
sleep 2

# 输出当前 WSL eth0 IP（hostname -I 第一项）
ip=$(hostname -I | awk '{print $1}')
echo "[IP] WSL 当前 IP = $ip"

# 输出端口探活结果
for port in 3306 6379; do
    if (echo > "/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
        echo "[PORT] 127.0.0.1:$port OK"
    else
        echo "[PORT] 127.0.0.1:$port FAIL"
    fi
done

if [ ${#missing[@]} -gt 0 ]; then
    echo "" >&2
    echo "[ERR] 容器缺失，请手动创建: ${missing[*]}" >&2
    echo "      参考命令: docker run -d --name mysql-dev -p 3306:3306 -e MYSQL_ROOT_PASSWORD=123123 -e MYSQL_DATABASE=zg_auth mysql:8.0" >&2
    echo "                docker run -d --name redis-dev -p 6379:6379 redis:7" >&2
    exit 2
fi

exit 0
