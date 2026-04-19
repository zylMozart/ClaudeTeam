---
name: server-status
description: "一键查看服务器状态：容器数量与资源占用、主机 CPU/内存/磁盘/网络 负载。用法：/server-status"
---

# 服务器状态一键巡检

本 skill 用于一次性输出：
1. 容器总数与清单（运行中 / 已停止 / 异常退出）
2. 每个容器资源占用（CPU / 内存 / 网络 IO / 块 IO / 文件系统大小 / 健康状态）
3. 主机整体负载（CPU、内存、磁盘、网络）
4. 系统基础信息（主机名 / 内核 / 时间同步 / 登录数）

## 执行原则
- **只读命令**：全部不改变系统状态。
- **容错**：每条命令加 `2>/dev/null || true`，单点失败不阻塞。
- **无 Docker / 无 sysstat 环境自动跳过该段**，不要报错。
- **顺序**：容器层 → 主机资源 → 网络 → 系统基础。
- **输出分段标题**（`=== 容器 ===` 等），方便阅读。
- **关键阈值高亮**：
  - 磁盘使用率 > 80%
  - 内存可用 < 10%
  - 负载均值 > CPU 核心数
  - 容器 RestartCount 快速增长
  - 容器 health=unhealthy
- **末尾输出一行摘要**：`摘要: 容器 N 运行 / 负载 x.xx / 内存 xx% / 磁盘最高 xx%`

## 执行步骤

### === 容器 ===
    docker ps -q | wc -l
    docker ps -aq | wc -l
    docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}'
    docker ps -a --filter 'status=exited' --format 'table {{.Names}}\t{{.Status}}\t{{.ExitCode}}'

### === 容器资源 ===
    docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}\t{{.PIDs}}'
    docker ps -s --format 'table {{.Names}}\t{{.Size}}'
    ids=$(docker ps -q); [ -n "$ids" ] && docker inspect --format '{{.Name}} restart={{.RestartCount}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}} started={{.State.StartedAt}}' $ids

**必须带 --no-stream**，否则 docker stats 会阻塞。

### === CPU ===
    uptime
    nproc
    top -bn1 | head -20
    mpstat 1 1      # 可选

### === 内存 ===
    free -h
    head -5 /proc/meminfo
    dmesg -T | grep -iE 'oom|killed process' | tail -5

### === 磁盘 ===
    df -hT -x tmpfs -x devtmpfs -x overlay
    df -i -x tmpfs -x devtmpfs
    iostat -xz 1 2 | tail -n +4    # 可选
    du -sh /var/lib/docker

### === 网络 ===
    ip -br addr
    ss -s
    ss -tunlp | head -20
    ip -s link

### === 系统 ===
    hostname
    uname -r
    date
    timedatectl status | grep -iE 'sync|ntp'
    who | wc -l

## 输出格式

    === 摘要 ===
        运行容器：<N> / 总容器：<M>
        负载(1m): <v>  核心数：<c>  {>核心数时标 ⚠}
        内存可用：<X%>                {<10% 时标 ⚠}
        磁盘最高使用率：<分区> <X%>    {>80% 时标 ⚠}
        异常容器：<列出 restart 快速增长或 unhealthy 的; 若无则写"无">

## 依赖
- 必需：docker、coreutils、procps、iproute2
- 可选：sysstat（mpstat / iostat），缺失时跳过对应段落

## 权限提示
若 docker 相关命令全部失败：提示用户加 docker 组或用 sudo 重试，继续执行主机层命令。
