FROM docker.n8n.io/n8nio/n8n:latest
USER root
RUN npm install -g mongodb nodemailer axios
COPY docker/n8n-entrypoint.sh /n8n-entrypoint.sh
RUN chmod +x /n8n-entrypoint.sh
USER node
ENTRYPOINT ["/n8n-entrypoint.sh"]
