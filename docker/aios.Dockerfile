FROM node:20-alpine
WORKDIR /app
COPY aios-dashboard/package.json .
RUN npm install --omit=dev
COPY aios-dashboard/ .
EXPOSE 3000
CMD ["node", "server.js"]
