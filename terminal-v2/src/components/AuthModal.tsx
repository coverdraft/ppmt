import { useState } from 'react';

interface AuthModalProps {
  /** Called when user submits valid credentials */
  onConnect: (sessionPassword: string, apiKey: string, apiSecret: string, allocatedUsdt: number) => void;
  /** Called when user cancels */
  onCancel: () => void;
  /** Current connection status */
  status: 'idle' | 'connecting' | 'authenticating' | 'connected' | 'error';
  /** Error message from last attempt */
  error: string | null;
}

/**
 * AuthModal — Centered modal for MEXC Futures credentials.
 *
 * v0.47.0: ENTREGABLE 8
 *
 * Dark terminal aesthetic, red accent for LIVE mode.
 * Fields: Session Password, API Key, Secret Key, Capital to Trade (USDT).
 * Validates all fields non-empty before enabling the connect button.
 */
export default function AuthModal({ onConnect, onCancel, status, error }: AuthModalProps) {
  const [sessionPassword, setSessionPassword] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [apiSecret, setApiSecret] = useState('');
  const [allocatedUsdt, setAllocatedUsdt] = useState('50');
  const [showSecret, setShowSecret] = useState(false);

  const usdtValue = parseFloat(allocatedUsdt);
  const isValid =
    sessionPassword.trim() !== '' &&
    apiKey.trim() !== '' &&
    apiSecret.trim() !== '' &&
    !isNaN(usdtValue) &&
    usdtValue > 0;
  const isWorking = status === 'connecting' || status === 'authenticating';

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (isValid && !isWorking) {
      onConnect(sessionPassword.trim(), apiKey.trim(), apiSecret.trim(), usdtValue);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
        onClick={onCancel}
      />

      {/* Modal Card */}
      <div className="relative z-10 w-full max-w-md mx-4 bg-[#0d0d14] border border-red-900/40 rounded-xl shadow-2xl shadow-red-900/20">
        {/* Header */}
        <div className="px-6 pt-5 pb-3 border-b border-red-900/30">
          <div className="flex items-center gap-3">
            <span className="w-3 h-3 rounded-full bg-red-500 animate-pulse flex-shrink-0" />
            <h2 className="font-mono font-bold text-white text-lg tracking-tight">
              LIVE TRADING
            </h2>
          </div>
          <p className="text-xs text-gray-500 font-mono mt-1 ml-6">
            MEXC Futures — Las credenciales se cifran antes de enviarse
          </p>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="px-6 py-5 space-y-4">
          {/* Session Password */}
          <div>
            <label className="block text-xs font-mono text-gray-400 mb-1.5 uppercase tracking-wider">
              Session Password
            </label>
            <input
              type="password"
              value={sessionPassword}
              onChange={(e) => setSessionPassword(e.target.value)}
              placeholder="Clave maestra para cifrar"
              className="w-full px-3 py-2.5 bg-[#0a0a0f] border border-gray-800 rounded-lg text-white font-mono text-sm placeholder-gray-700 focus:outline-none focus:border-red-500/50 focus:ring-1 focus:ring-red-500/20 transition-colors"
              disabled={isWorking}
              autoFocus
            />
            <span className="text-[10px] text-gray-600 font-mono mt-1 block">
              Se usa para cifrar tus API keys en tránsito (Fernet/PBKDF2)
            </span>
          </div>

          {/* API Key */}
          <div>
            <label className="block text-xs font-mono text-gray-400 mb-1.5 uppercase tracking-wider">
              MEXC API Key
            </label>
            <input
              type="text"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="mx0..."
              className="w-full px-3 py-2.5 bg-[#0a0a0f] border border-gray-800 rounded-lg text-white font-mono text-sm placeholder-gray-700 focus:outline-none focus:border-red-500/50 focus:ring-1 focus:ring-red-500/20 transition-colors"
              disabled={isWorking}
            />
          </div>

          {/* Secret Key */}
          <div>
            <label className="block text-xs font-mono text-gray-400 mb-1.5 uppercase tracking-wider">
              MEXC Secret Key
            </label>
            <div className="relative">
              <input
                type={showSecret ? 'text' : 'password'}
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value)}
                placeholder="Tu clave secreta"
                className="w-full px-3 py-2.5 pr-16 bg-[#0a0a0f] border border-gray-800 rounded-lg text-white font-mono text-sm placeholder-gray-700 focus:outline-none focus:border-red-500/50 focus:ring-1 focus:ring-red-500/20 transition-colors"
                disabled={isWorking}
              />
              <button
                type="button"
                onClick={() => setShowSecret(!showSecret)}
                className="absolute right-2 top-1/2 -translate-y-1/2 px-2 py-1 text-[10px] font-mono text-gray-500 hover:text-gray-300 transition-colors"
              >
                {showSecret ? 'OCULTAR' : 'MOSTRAR'}
              </button>
            </div>
          </div>

          {/* Capital to Trade (USDT) — ENTREGABLE 8 */}
          <div>
            <label className="block text-xs font-mono text-gray-400 mb-1.5 uppercase tracking-wider">
              Capital a operar (USDT)
            </label>
            <input
              type="number"
              min="1"
              step="1"
              value={allocatedUsdt}
              onChange={(e) => setAllocatedUsdt(e.target.value)}
              placeholder="50"
              className="w-full px-3 py-2.5 bg-[#0a0a0f] border border-gray-800 rounded-lg text-white font-mono text-sm placeholder-gray-700 focus:outline-none focus:border-red-500/50 focus:ring-1 focus:ring-red-500/20 transition-colors"
              disabled={isWorking}
            />
            <span className="text-[10px] text-gray-600 font-mono mt-1 block">
              Monto en USDT asignado a cada operación (tamaño de posición dinámico)
            </span>
          </div>

          {/* Error message */}
          {error && (
            <div className="px-3 py-2 bg-red-900/20 border border-red-800/30 rounded-lg text-red-400 text-xs font-mono">
              {error}
            </div>
          )}

          {/* Buttons */}
          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={onCancel}
              className="flex-1 px-4 py-2.5 bg-gray-800/50 border border-gray-700/50 rounded-lg text-gray-400 font-mono text-xs font-semibold hover:bg-gray-800 transition-colors"
              disabled={isWorking}
            >
              CANCELAR
            </button>
            <button
              type="submit"
              disabled={!isValid || isWorking}
              className={`flex-1 px-4 py-2.5 rounded-lg font-mono text-xs font-bold transition-all duration-200 ${
                isValid && !isWorking
                  ? 'bg-red-600 text-white hover:bg-red-500 shadow-lg shadow-red-600/30'
                  : 'bg-gray-800/50 text-gray-600 cursor-not-allowed'
              }`}
            >
              {isWorking ? (
                <span className="flex items-center justify-center gap-2">
                  <span className="w-2 h-2 rounded-full bg-white animate-pulse" />
                  {status === 'connecting' ? 'CONECTANDO...' : 'AUTENTICANDO...'}
                </span>
              ) : (
                'CONECTAR A MEXC'
              )}
            </button>
          </div>
        </form>

        {/* Footer security note */}
        <div className="px-6 py-3 border-t border-gray-800/50 bg-[#080810] rounded-b-xl">
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-gray-600 font-mono">
              Tus keys se cifran con Fernet (AES-128-CBC + HMAC-SHA256) antes de salir de tu navegador.
              Nunca se almacenan en texto plano.
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
