import { useEffect, useState } from 'react';
import * as Progress from '@radix-ui/react-progress';
import { Loader2, Zap, Database, Microscope, Activity } from 'lucide-react';

interface ProcessingViewProps {
  onComplete: () => void;
}

export function ProcessingView({ onComplete }: ProcessingViewProps) {
  const [progress, setProgress] = useState(0);
  const [statusMessage, setStatusMessage] = useState("Iniciando motor de fatiamento...");
  const [stage, setStage] = useState("Setup");

  useEffect(() => {
    const pollStatus = async () => {
      try {
        const response = await fetch('http://localhost:8000/status');
        const data = await response.json();

        setProgress(data.progress);
        setStatusMessage(data.message);

        if (data.progress < 15) setStage("Leitura CZI");
        else if (data.progress < 60) setStage("Fatiamento");
        else if (data.progress < 95) setStage("Processamento Baseado em Filtro de Cor");
        else setStage("Finalizando");

        if (data.status === 'complete' || data.progress >= 100) {
          clearInterval(interval);
          setTimeout(() => onComplete(), 1000);
        }
      } catch (err) {
        console.error("Erro de conexão com o backend:", err);
      }
    };

    const interval = setInterval(pollStatus, 800);
    return () => clearInterval(interval);
  }, [onComplete]);

  return (
    <div className="flex flex-col items-center justify-center gap-12 p-12 h-full bg-transparent">
      
      {/* Elemento Visual Central */}
      <div className="relative w-80 h-80 flex items-center justify-center">
        <div className="absolute inset-0 rounded-full bg-blue-500/5 animate-pulse border border-blue-500/10" />
        <div className="absolute inset-8 rounded-full bg-teal-500/5 animate-pulse border border-teal-500/10" style={{ animationDelay: '0.5s' }} />
        
        <div className="absolute w-40 h-40 rounded-[2.5rem] bg-gradient-to-br from-blue-600 via-blue-500 to-teal-400 animate-spin shadow-[0_0_60px_rgba(59,130,246,0.3)]" style={{ animationDuration: '6s' }}>
          {/* Ajuste de fundo interno para suportar dark/light mode */}
          <div className="w-full h-full rounded-[2.5rem] bg-white dark:bg-slate-950 m-1 flex items-center justify-center">
             <Microscope className="w-14 h-14 text-blue-400 animate-bounce" />
          </div>
        </div>
        
        {/* Porcentagem Flutuante */}
        <div className="absolute top-0 right-0 bg-white dark:bg-slate-900 border border-slate-200 dark:border-white/10 px-4 py-2 rounded-2xl shadow-2xl">
          <span className="text-2xl font-black text-slate-900 dark:text-white tabular-nums">{progress}%</span>
        </div>
      </div>

      {/* Área de Progresso e Mensagens */}
      <div className="w-full max-w-xl space-y-6">
        <div className="space-y-2 text-center">
          <div className="flex items-center justify-center gap-2 text-blue-600 dark:text-blue-400 font-bold uppercase tracking-widest text-xs">
            <Loader2 className="w-3 h-3 animate-spin" /> {stage}
          </div>
          <h2 className="text-2xl font-semibold text-slate-900 dark:text-white tracking-tight">
            {statusMessage}
          </h2>
        </div>

        <Progress.Root
          className="relative overflow-hidden bg-slate-200 dark:bg-slate-900 rounded-full w-full h-4 border border-slate-300 dark:border-white/5 shadow-inner"
          value={progress}
        >
          <Progress.Indicator
            className="h-full bg-gradient-to-r from-blue-600 to-teal-400 transition-transform duration-500 ease-out rounded-full"
            style={{ transform: `translateX(-${100 - progress}%)` }}
          />
        </Progress.Root>

        {/* Rodapé Técnico */}
        <div className="grid grid-cols-3 gap-4 pt-8 border-t border-slate-200 dark:border-white/5">
           <div className="flex items-center gap-3 text-slate-400">
              <Zap className="w-4 h-4 text-amber-500" />
              <div className="text-[10px] uppercase font-bold tracking-tighter">
                 <p>Processamento</p>
                 <p className="text-slate-900 dark:text-white">WSL Parallel</p>
              </div>
           </div>
           <div className="flex items-center gap-3 text-slate-400">
              <Database className="w-4 h-4 text-blue-500" />
              <div className="text-[10px] uppercase font-bold tracking-tighter">
                 <p>Armazenamento</p>
                 <p className="text-slate-900 dark:text-white">Fatiamento local</p>
              </div>
           </div>
           <div className="flex items-center gap-3 text-slate-400 justify-end text-right">
              <div className="text-[10px] uppercase font-bold tracking-tighter">
                 <p>Versão</p>
                 <p className="text-teal-600 dark:text-teal-500">RockFace Alfa</p>
              </div>
              <Activity className="w-4 h-4 text-teal-500" />
           </div>
        </div>
      </div>
    </div>
  );
}