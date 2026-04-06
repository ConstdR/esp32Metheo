FROM alpine
RUN apk add --no-cache python3 py3-jinja2 py3-dateutil py3-aiohttp tzdata
ENV TZ=Europe/Prague
WORKDIR /serv
EXPOSE 8088 1883
CMD ["sh", "-c", "exec ./web.py >>log 2>&1"]
