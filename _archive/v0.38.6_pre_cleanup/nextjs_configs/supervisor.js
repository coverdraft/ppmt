const { spawn } = require('child_process');
const path = require('path');

function startServer() {
  console.log(`[${new Date().toISOString()}] Starting dev server...`);
  const child = spawn('bun', ['run', 'dev'], {
    cwd: '/home/z/my-project',
    stdio: 'inherit',
    env: { ...process.env },
  });
  
  child.on('exit', (code, signal) => {
    console.log(`[${new Date().toISOString()}] Server exited code=${code} signal=${signal}. Restarting in 3s...`);
    setTimeout(startServer, 3000);
  });
  
  child.on('error', (err) => {
    console.error(`[${new Date().toISOString()}] Error:`, err.message);
    setTimeout(startServer, 3000);
  });
}

startServer();
