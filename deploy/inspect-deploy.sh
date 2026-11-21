#!/usr/bin/env bash
# 只读诊断：查清现有容器把数据挂在哪（/data、/opt、还是根目录）。
# 不修改、不删除、不重启任何东西，纯 docker inspect / df / du。
#
# 用法：
#   bash inspect-deploy.sh
#   bash inspect-deploy.sh > /tmp/deploy-report.txt   # 存一份回看

set -u

KEYWORDS='paro|mysekai|akito|pjsk|intel|nonebot'

echo "==================== 1. Docker 基本信息 ===================="
docker version --format 'Server: {{.Server.Version}}' 2>/dev/null || echo "docker 命令不可用"
docker info --format 'Docker Root Dir: {{.DockerRootDir}}' 2>/dev/null || true
echo

echo "==================== 2. 运行中的容器 ===================="
docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null || true
echo

echo "==================== 3. 关键容器挂载明细 ===================="
CONTAINERS=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -iE "$KEYWORDS" || true)
if [ -z "$CONTAINERS" ]; then
  echo "没匹配到关键词（$KEYWORDS）的容器，下面列全部容器的挂载："
  CONTAINERS=$(docker ps --format '{{.Names}}' 2>/dev/null || true)
fi
for name in $CONTAINERS; do
  echo "--- 容器: $name ---"
  docker inspect "$name" --format '{{range .Mounts}}  [{{.Type}}] {{.Source}}  ==>  {{.Destination}}{{println}}{{end}}' 2>/dev/null || true
  docker inspect "$name" --format '  compose 项目: {{index .Config.Labels "com.docker.compose.project"}}' 2>/dev/null || true
  docker inspect "$name" --format '  compose 目录: {{index .Config.Labels "com.docker.compose.project.working_dir"}}' 2>/dev/null || true
  docker inspect "$name" --format '  compose 文件: {{index .Config.Labels "com.docker.compose.project.config_files"}}' 2>/dev/null || true
  echo
done

echo "==================== 4. compose 项目清单 ===================="
docker compose ls 2>/dev/null || docker-compose ls 2>/dev/null || true
echo

echo "==================== 5. 磁盘布局（根 vs 数据盘）===================="
df -h / 2>/dev/null || true
df -h /data 2>/dev/null || echo "/data 不存在"
echo

echo "==================== 6. 常见部署目录占用 ===================="
for d in /data /opt /root /srv /var/lib/docker; do
  if [ -d "$d" ]; then
    printf '%-16s ' "$d"
    du -sh "$d" 2>/dev/null | awk '{print $1}' || echo "无权限/不存在"
  fi
done
echo

echo "==================== 7. 挂载源目录里有什么 ===================="
for d in /data /opt /opt/mysekai-data; do
  if [ -d "$d" ]; then
    echo "--- $d ---"
    ls -la "$d" 2>/dev/null | head -20
  fi
done
echo

echo "==================== 8. 容器内数据目录实际大小 ===================="
for name in $CONTAINERS; do
  echo "--- $name ---"
  docker exec "$name" sh -c 'du -sh /data /app/data /bot-data 2>/dev/null' 2>/dev/null || echo "  （容器无 sh 或无这些目录）"
done
echo

echo "==================== 完成 ===================="
echo "把上面输出整段回贴，就能确定每个容器到底挂 /data 还是根目录。"
