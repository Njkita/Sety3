FROM alpine:3.20

RUN apk add --no-cache openssl bash

WORKDIR /meshctl
COPY generate.sh ./generate.sh
RUN chmod +x ./generate.sh

CMD ["./generate.sh"]
