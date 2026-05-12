import { useState } from 'react';
import * as Tabs from '@radix-ui/react-tabs';
import * as Progress from '@radix-ui/react-progress';
import {
  Upload,
  Image,
  BarChart3,
  Loader2,
  Zap,
  Database,
  Microscope,
  Activity,
} from 'lucide-react';

import { UploadSection } from './components/UploadSection';
import { ProcessingView } from './components/ProcessingView';
import { ImageViewer } from './components/ImageViewer';
import { Dashboard } from './components/Dashboard';
import logoDev from './components/assets/logo_dev.png';

type AppState = 'upload' | 'starting' | 'processing' | 'results';

const API_BASE_URL = 'http://localhost:8000';

function StartingView({ message }: { message: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-12 p-12 h-full bg-transparent">
      {/* Elemento visual central — semelhante ao ProcessingView */}
      <div className="relative w-80 h-80 flex items-center justify-center">
        <div className="absolute inset-0 rounded-full bg-blue-500/5 animate-pulse border border-blue-500/10" />

        <div
          className="absolute inset-8 rounded-full bg-teal-500/5 animate-pulse border border-teal-500/10"
          style={{ animationDelay: '0.5s' }}
        />

        <div
          className="absolute w-40 h-40 rounded-[2.5rem] bg-gradient-to-br from-blue-600 via-blue-500 to-teal-400 animate-spin shadow-[0_0_60px_rgba(59,130,246,0.3)]"
          style={{ animationDuration: '6s' }}
        >
          <div className="w-full h-full rounded-[2.5rem] bg-white dark:bg-slate-950 m-1 flex items-center justify-center">
            <Microscope className="w-14 h-14 text-blue-400 animate-bounce" />
          </div>
        </div>

        <div className="absolute top-0 right-0 bg-white dark:bg-slate-900 border border-slate-200 dark:border-white/10 px-4 py-2 rounded-2xl shadow-2xl">
          <Loader2 className="w-7 h-7 text-blue-500 animate-spin" />
        </div>
      </div>

      {/* Área de mensagem e progresso */}
      <div className="w-full max-w-xl space-y-6">
        <div className="space-y-2 text-center">
          <div className="flex items-center justify-center gap-2 text-blue-600 dark:text-blue-400 font-bold uppercase tracking-widest text-xs">
            <Loader2 className="w-3 h-3 animate-spin" />
            Preparando
          </div>

          <h2 className="text-2xl font-semibold text-slate-900 dark:text-white tracking-tight">
            Preparando nova análise
          </h2>

          <p className="text-sm text-muted-foreground">
            {message}
          </p>
        </div>

        <Progress.Root
          className="relative overflow-hidden bg-slate-200 dark:bg-slate-900 rounded-full w-full h-4 border border-slate-300 dark:border-white/5 shadow-inner"
          value={35}
        >
          <Progress.Indicator
            className="h-full bg-gradient-to-r from-blue-600 to-teal-400 transition-transform duration-500 ease-out rounded-full"
            style={{ transform: `translateX(-65%)` }}
          />
        </Progress.Root>

        {/* Rodapé técnico — mesmo alinhamento do ProcessingView */}
        <div className="grid grid-cols-3 gap-4 pt-8 border-t border-slate-200 dark:border-white/5">
          <div className="flex items-center gap-3 text-slate-400">
            <Zap className="w-4 h-4 text-amber-500" />
            <div className="text-[10px] uppercase font-bold tracking-tighter">
              <p>Processamento</p>
              <p className="text-slate-900 dark:text-white">
                Inicialização
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3 text-slate-400">
            <Database className="w-4 h-4 text-blue-500" />
            <div className="text-[10px] uppercase font-bold tracking-tighter">
              <p>Sessão</p>
              <p className="text-slate-900 dark:text-white">
                Verificação local
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3 text-slate-400 justify-end text-right">
            <div className="text-[10px] uppercase font-bold tracking-tighter">
              <p>Versão</p>
              <p className="text-teal-600 dark:text-teal-500">
                RockFace Alfa
              </p>
            </div>
            <Activity className="w-4 h-4 text-teal-500" />
          </div>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [appState, setAppState] = useState<AppState>('upload');
  const [activeTab, setActiveTab] = useState('viewer');
  const [statusMessage, setStatusMessage] = useState(
    'Aguardando seleção do arquivo...'
  );

  const handleFileUpload = async (file: File, channels: string[]) => {
    setAppState('starting');
    setStatusMessage('Verificando se já existem resultados para este arquivo...');

    try {
      const checkResponse = await fetch(
        `${API_BASE_URL}/check-results?filename=${encodeURIComponent(file.name)}`
      );

      if (checkResponse.ok) {
        const checkData = await checkResponse.json();

        if (checkData.exists === true || checkData.status === 'restored') {
          setStatusMessage('Sessão anterior restaurada.');
          setActiveTab('viewer');
          setAppState('results');
          return;
        }
      }

      setStatusMessage(
        'Nenhum resultado prévio encontrado. Limpando a sessão anterior e enviando o novo arquivo.'
      );

      const formData = new FormData();
      formData.append('file', file);
      formData.append('channels', JSON.stringify(channels));

      const uploadResponse = await fetch(`${API_BASE_URL}/upload`, {
        method: 'POST',
        body: formData,
      });

      if (!uploadResponse.ok) {
        throw new Error('Falha no upload');
      }

      const uploadData = await uploadResponse.json();

      if (uploadData.status === 'restored') {
        setStatusMessage('Sessão anterior restaurada.');
        setActiveTab('viewer');
        setAppState('results');
        return;
      }

      if (uploadData.status === 'started') {
        setStatusMessage('Processamento iniciado.');
        setAppState('processing');
        return;
      }

      throw new Error(uploadData.message || 'Resposta inesperada do backend');
    } catch (error) {
      console.error('Erro:', error);
      setStatusMessage('Erro ao iniciar o processamento.');
      setAppState('upload');
      alert('Erro ao iniciar o processamento.');
    }
  };

  const handleProcessingComplete = () => {
    setActiveTab('viewer');
    setAppState('results');
  };

  const handleNewAnalysis = () => {
    setStatusMessage('Aguardando seleção do arquivo...');
    setAppState('upload');
  };

  return (
    <div className="size-full flex flex-col bg-gradient-to-br from-slate-50 via-blue-50 to-teal-50 dark:from-slate-900 dark:via-blue-950 dark:to-teal-950">
      <header className="flex items-center justify-between px-8 py-6 border-b border-border/50 bg-background/30 backdrop-blur-xl">
        <div className="flex items-center gap-4">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-blue-600 to-teal-600 flex items-center justify-center shadow-lg">
            <span className="text-2xl">🪨</span>
          </div>

          <div>
            <h1 className="text-2xl tracking-tight bg-gradient-to-r from-blue-600 to-teal-600 bg-clip-text text-transparent">
              ROCKFACE
            </h1>
            <p className="text-sm text-muted-foreground">
              Análise de Lâminas Petrográficas
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <div className="px-3 py-1.5 rounded-full bg-blue-500/10 border border-blue-500/30">
            Petrobras
          </div>
        </div>
      </header>

      <main className="flex-1 overflow-hidden">
        {appState === 'upload' && (
          <div className="h-full flex items-center justify-center">
            <UploadSection onFileUpload={handleFileUpload} />
          </div>
        )}

        {appState === 'starting' && (
          <StartingView message={statusMessage} />
        )}

        {appState === 'processing' && (
          <ProcessingView onComplete={handleProcessingComplete} />
        )}

        {appState === 'results' && (
          <Tabs.Root
            value={activeTab}
            onValueChange={setActiveTab}
            className="h-full flex flex-col"
          >
            <Tabs.List className="flex gap-2 px-8 py-4 border-b border-border/50 bg-background/30 backdrop-blur-xl">
              <Tabs.Trigger
                value="viewer"
                className="flex items-center gap-2 px-6 py-3 rounded-xl transition-all data-[state=active]:bg-gradient-to-r data-[state=active]:from-blue-600 data-[state=active]:to-teal-600 data-[state=active]:text-white hover:bg-accent"
                style={{
                  boxShadow:
                    activeTab === 'viewer'
                      ? '0 4px 16px rgba(59, 130, 246, 0.3)'
                      : 'none',
                }}
              >
                <Image className="w-5 h-5" />
                <span>Visualizador</span>
              </Tabs.Trigger>

              <Tabs.Trigger
                value="dashboard"
                className="flex items-center gap-2 px-6 py-3 rounded-xl transition-all data-[state=active]:bg-gradient-to-r data-[state=active]:from-blue-600 data-[state=active]:to-teal-600 data-[state=active]:text-white hover:bg-accent"
                style={{
                  boxShadow:
                    activeTab === 'dashboard'
                      ? '0 4px 16px rgba(59, 130, 246, 0.3)'
                      : 'none',
                }}
              >
                <BarChart3 className="w-5 h-5" />
                <span>Dashboard</span>
              </Tabs.Trigger>

              <button
                onClick={handleNewAnalysis}
                className="ml-auto flex items-center gap-2 px-6 py-3 rounded-xl bg-muted/50 hover:bg-muted transition-all"
              >
                <Upload className="w-5 h-5" />
                <span>Nova Análise</span>
              </button>
            </Tabs.List>

            <Tabs.Content
              value="viewer"
              forceMount
              className="flex-1 overflow-hidden data-[state=inactive]:hidden"
            >
              <ImageViewer />
            </Tabs.Content>

            <Tabs.Content
              value="dashboard"
              forceMount
              className="flex-1 overflow-hidden data-[state=inactive]:hidden"
            >
              <Dashboard />
            </Tabs.Content>
          </Tabs.Root>
        )}
      </main>

      <footer className="flex items-center justify-center px-8 py-4 border-t border-border/50 bg-background/30 backdrop-blur-xl">
        <div className="opacity-100 hover:opacity-100 transition-opacity duration-300">
          <img
            src={logoDev}
            alt="Logo"
            className="h-15 w-auto object-contain"
          />
        </div>
      </footer>
    </div>
  );
}