FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 只复制程序文件，账号、Cookie、挂单数据等本地文件不打进镜像。
COPY app.py steam_session.py index.html ./

# 数据保存到挂载卷里持久化：secret.json / watchlist.json / steam_login.json / settings.json
ENV SKINDESK_DATA=/data
ENV SKINDESK_HOST=0.0.0.0
VOLUME ["/data"]
EXPOSE 8777

CMD ["python", "app.py"]
