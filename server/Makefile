help:
	echo ${HELPSTR}

run:
	docker run -v ${PWD}:/serv --restart always -dit --network host --name espMetheo esp-metheo

run-bridge:
	docker run -v ${PWD}:/serv --restart always -dit -p 8088:8088 -p 1883:1883/udp --name espMetheo esp-metheo

restart: stop rm build run

stop:
	docker container stop espMetheo

rm:
	docker container rm espMetheo

attach:
	docker container attach espMetheo

define HELPSTR
run         -- run with host network
run-bridge  -- run with bridge network
restart     -- stop, remove, rebuild and start
stop        -- stop
rm          -- remove container
attach      -- attch to running container
endef

define LOGROTATE
${PWD}/log
{
	missingok
	notifempty
	copytruncate
}
endef
export LOGROTATE

build:
	docker build -t esp-metheo .
	@echo "$$LOGROTATE" >espMetheo.logrotate

# vim: noet
