FROM node:20-alpine
WORKDIR /app
COPY package.json bun.lock ./
RUN npm install
COPY . .
EXPOSE 3003 3010
CMD ["node", "index.ts"]
