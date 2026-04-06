NAME  = espMetheo
IMAGE = esp-metheo

define HELPSTR
run         -- run with host network
run-bridge  -- run with bridge network
restart     -- stop, remove, rebuild and start
stop        -- stop container
rm          -- remove container
shell       -- run shell on running container
attach      -- attach to running container
endef
export HELPSTR

define LOGROTATE
${PWD}/log
{
	missingok
	notifempty
	copytruncate
}
endef
export LOGROTATE

all: help
help: ; @echo "$$HELPSTR"
build:
	docker build -t $(IMAGE) .
	@echo "$$LOGROTATE" >$(NAME).logrotate
run:
	docker run -v ${PWD}:/serv --restart always -dit --network host --name $(NAME) $(IMAGE)
run-bridge:
	docker run -v ${PWD}:/serv --restart always -dit -p 8088:8088 -p 1883:1883/udp --name $(NAME) $(IMAGE)
restart: stop rm build run
stop:   ; -docker container stop   $(NAME)
rm:     ; -docker container rm     $(NAME)
attach: ; docker container attach $(NAME)
shell:  ; docker exec -it $(NAME) /bin/sh

# vim: noet
