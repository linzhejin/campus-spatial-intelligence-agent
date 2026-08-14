# 漫步珞珈 · 部署指南（腾讯云 / 阿里云）

本指南教你用国内云服务器（腾讯云/阿里云，微信/支付宝支付）把漫步珞珈部署到公网，让所有人都能访问。

> 为什么用国内服务器：国外平台（Render/Fly.io/Railway）要绑国外信用卡，大陆银行卡付不了；Cloudflare Tunnel 免费但大陆访问不稳定。国内轻量服务器是唯一「大陆支付 + 稳定」的方案。

---

## 一、购买服务器（约 10 分钟）

1. 打开 [腾讯云轻量应用服务器](https://cloud.tencent.com/product/lighthouse)（或阿里云轻量）
2. 地域选离你近的（上海/广州）
3. **系统镜像选 `Ubuntu 22.04`**
4. 配置 **2核2G** 足够（项目精简后 256MB 内存就能跑）
5. 付款后记下 **公网 IP**

> 学生可买「学生机」或新人首单，价格低至 10~20 元/月。

## 二、放行防火墙端口

在云控制台的「防火墙」里添加规则：

| 协议 | 端口 | 来源 |
|------|------|------|
| TCP  | 5000 | 所有来源（0.0.0.0/0） |

## 三、登录服务器并部署（约 5 分钟）

用 SSH 登录服务器（Windows 用 PowerShell，Mac 用终端）：

```bash
ssh ubuntu@你的公网IP
```

登录后，依次执行：

```bash
# 1. 克隆代码
git clone https://github.com/linzhejin/campus-spatial-intelligence-agent.git
cd campus-spatial-intelligence-agent

# 2. 一键部署（自动装环境、装依赖、建服务、启动）
bash deploy.sh
```

脚本跑到第 4 步会停下来，提示你填密钥：

```bash
nano .env    # 填入 DEEPSEEK_API_KEY / AMAP_KEY / AMAP_SECURITY_CODE
```

填完保存（`Ctrl+O` 回车，`Ctrl+X` 退出），重新运行：

```bash
bash deploy.sh
```

部署完成会显示 `✅ 部署完成`。

## 四、访问

浏览器打开：

```
http://你的公网IP:5000
```

把地址发到微信里，别人也能点开。

## 五、常用运维命令

```bash
# 查看服务状态
sudo systemctl status whu-walker

# 查看实时日志（排查问题）
sudo journalctl -u whu-walker -f

# 重启服务（改了代码后）
sudo systemctl restart whu-walker

# 更新代码到最新版
cd ~/campus-spatial-intelligence-agent
git pull
sudo systemctl restart whu-walker
```

## 六、常见问题

**Q：访问不了，端口没通？**
检查两处防火墙都放行了 5000：① 云控制台的防火墙 ② 服务器内 `sudo ufw allow 5000`（Ubuntu 默认 ufw 未启用，一般不需要）。

**Q：地图加载不出来？**
检查 `.env` 里 `AMAP_KEY` 和 `AMAP_SECURITY_CODE` 都填了。这两个 key 从 [高德开放平台](https://console.amap.com/) 申请（JS API 2.0 类型）。

**Q：对话报错？**
检查 `.env` 里 `DEEPSEEK_API_KEY` 填了，且 DeepSeek 账户有余额。

**Q：想绑域名 + HTTPS？**
需要先 ICP 备案（约 1-2 周），备案通过后把域名解析到服务器 IP，再用 `certbot` 配 HTTPS。起步阶段用 IP 直连即可，无需备案。

---

## 附：手动部署（不用脚本，理解原理）

```bash
# 装系统依赖
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git

# 建虚拟环境 + 装包
cd campus-spatial-intelligence-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 配 .env（填密钥 + FLASK_ENV=production）
cp .env.example .env && nano .env

# 前台试跑（验证没问题后 Ctrl+C）
gunicorn app:app --workers 1 --timeout 60 --bind 0.0.0.0:5000

# 后台常驻（systemd，见 deploy.sh 里的 service 配置）
```
