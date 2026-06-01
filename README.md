# 吉利银河 Home Assistant 集成

支持吉利银河车辆的 Home Assistant 自定义集成，提供：

- 车辆状态监控（电量、里程、胎压等）
- 远程控制（锁车、车窗、空调）
- 家桩充电管理
- 每日签到积分

## 支持车型

- 吉利银河 L7
- 吉利银河（geely2 平台）

## 安装

### 通过 HACS（推荐）

1. 确保已安装 [HACS](https://hacs.xyz/)
2. 进入 HACS → 集成 → 浏览并下载存储库
3. 搜索 "Geely Galaxy" 并安装
4. 重启 Home Assistant

### 手动安装

1. 将 `custom_components/geely_galaxy/` 复制到 Home Assistant 的 `custom_components/` 文件夹
2. 重启 Home Assistant

## 配置

进入 **设置 → 设备与服务 → 添加集成**，搜索"吉利银河"。

### 登录方式

1. **短信验证码（推荐）** - 输入手机号，完成验证
2. **Token 登录** - 输入从抓包获取的 refresh_token 和 device_sn
3. **密码登录** - 输入 SM4 加密后的密码

## 实体

### 传感器
- 车辆状态（电量、里程、胎压等）
- 签到状态
- 充电桩状态和记录

### 按钮
- 每日签到
- 车辆控制（车窗、空调等）

### 开关
- 车辆控制

## 问题排查

遇到问题请检查：
1. Home Assistant 日志（设置 → 系统 → 日志）
2. [提交 Issue](https://github.com/lyj5812/geely-galaxy/issues)

## 开发说明

本集成基于对吉利银河 APP API 的逆向分析。

## 许可证

MIT 许可证