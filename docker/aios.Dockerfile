FROM node:20-alpine
WORKDIR /app
COPY aios-dashboard/package.json .
RUN npm install --omit=dev
COPY aios-dashboard/ .
RUN mkdir -p /app/rag-knowledge-base/uploads
EXPOSE 3000
CMD ["node", "server.js"]
