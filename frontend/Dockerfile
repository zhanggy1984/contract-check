# 前端镜像（T4.5）：node 构建 → nginx 静态托管 + /api 反代
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --registry=https://registry.npmmirror.com
COPY . .
RUN npm run build

# M1：固定版本号（构建可复现），本地已通过镜像源拉取缓存
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
