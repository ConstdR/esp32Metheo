FROM alpine
RUN apk update
RUN apk add python3 py3-jinja2 py3-dateutil py3-aiohttp tzdata
ENV TZ=Europe/Prague

WORKDIR /serv

#RUN sed -i 's/ERROR/DEBUG/' /serv/config.cfg # DEBUG IT!

# run cotainer with volume mounted using -v ${PWD}:/serv

EXPOSE 8088 1883

CMD ./startbg.sh
