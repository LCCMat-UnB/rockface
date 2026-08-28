import { Upload, CheckCircle2, Layers, Loader2 } from 'lucide-react';
import { useState } from 'react';

interface UploadSectionProps {
  onFileUpload: (file: File, channels: string[]) => void | Promise<void>;
  isSubmitting?: boolean;
}

export function UploadSection({ onFileUpload, isSubmitting = false }: UploadSectionProps) {
  const [dragActive, setDragActive] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedChannels, setSelectedChannels] = useState<string[]>(['45', '90']);

  const channels = [
    { id: '15', label: '15°', color: 'bg-orange-500/20 border-orange-500/50' },
    { id: '30', label: '30°', color: 'bg-red-500/20 border-red-500/50' },
    { id: '45', label: '45°', color: 'bg-pink-500/20 border-pink-500/50' },
    { id: '60', label: '60°', color: 'bg-purple-500/20 border-purple-500/50' },
    { id: '75', label: '75°', color: 'bg-blue-500/20 border-blue-500/50' },
    { id: '90', label: '90°', color: 'bg-cyan-500/20 border-cyan-500/50' }
  ];

  const isCziFile = (file: File) => file.name.toLowerCase().endsWith('.czi');

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();

    if (e.type === 'dragenter' || e.type === 'dragover') {
      setDragActive(true);
    } else if (e.type === 'dragleave') {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const file = e.dataTransfer.files[0];

      if (isCziFile(file)) {
        setSelectedFile(file);
      } else {
        alert('Selecione um arquivo .CZI válido.');
      }
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    e.preventDefault();

    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];

      if (isCziFile(file)) {
        setSelectedFile(file);
      } else {
        setSelectedFile(null);
        alert('Selecione um arquivo .CZI válido.');
      }
    }
  };

  const toggleChannel = (channelId: string) => {
    setSelectedChannels(prev =>
      prev.includes(channelId)
        ? prev.filter(c => c !== channelId)
        : [...prev, channelId]
    );
  };

  const handleSubmit = () => {
    if (selectedFile && selectedChannels.length > 0 && !isSubmitting) {
      onFileUpload(selectedFile, selectedChannels);
    }
  };

  return (
    <div className="flex flex-col items-center justify-center gap-8 p-8 max-w-2xl mx-auto">
      <div
        className={`relative w-full rounded-3xl border-2 border-dashed transition-all duration-300 ${
          dragActive
            ? 'border-blue-400 bg-blue-500/10 scale-105'
            : 'border-border bg-card/50'
        } ${
          selectedFile ? 'border-green-400 bg-green-500/10' : ''
        }`}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
        style={{
          backdropFilter: 'blur(20px)',
          boxShadow: '0 8px 32px rgba(0, 0, 0, 0.1), inset 0 1px 1px rgba(255, 255, 255, 0.5)'
        }}
      >
        <div className="p-12 flex flex-col items-center justify-center gap-4">
          {selectedFile ? (
            <>
              <CheckCircle2 className="w-16 h-16 text-green-500" />
              <p className="text-lg text-foreground">{selectedFile.name}</p>
              <p className="text-sm text-muted-foreground">
                {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
              </p>
            </>
          ) : (
            <>
              <Upload className="w-16 h-16 text-muted-foreground" />
              <div className="text-center">
                <p className="text-lg text-foreground">
                  Arraste um arquivo CZI aqui
                </p>
                <p className="text-sm text-muted-foreground mt-2">
                  ou clique para selecionar
                </p>
              </div>
            </>
          )}
          <input
            type="file"
            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
            accept=".czi,.CZI"
            onChange={handleChange}
            disabled={isSubmitting}
          />
        </div>
      </div>

      <div className="w-full space-y-4">
        <div className="flex items-center gap-3 p-4 rounded-2xl bg-amber-500/10 border-2 border-amber-500/50" style={{
          backdropFilter: 'blur(10px)',
          boxShadow: '0 4px 16px rgba(0, 0, 0, 0.1)'
        }}>
          <div className="w-4 h-4 rounded-full bg-amber-500" />
          <span>0° (Luz Natural)</span>
        </div>

        <div className="flex items-center gap-2 mb-4">
          <Layers className="w-5 h-5 text-foreground" />
          <h3 className="text-lg">Selecione os canais de polarização adicionais</h3>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          {channels.map(channel => {
            const getChannelColor = (id: string) => {
              switch(id) {
                case '15': return 'bg-orange-500';
                case '30': return 'bg-red-500';
                case '45': return 'bg-pink-500';
                case '60': return 'bg-purple-500';
                case '75': return 'bg-blue-500';
                case '90': return 'bg-cyan-500';
                default: return 'bg-muted';
              }
            };

            return (
              <button
                key={channel.id}
                onClick={() => toggleChannel(channel.id)}
                disabled={isSubmitting}
                className={`p-4 rounded-2xl border-2 transition-all duration-300 disabled:opacity-60 disabled:cursor-not-allowed ${
                  selectedChannels.includes(channel.id)
                    ? `${channel.color} border-opacity-100 scale-105`
                    : 'bg-card/30 border-border border-opacity-50'
                }`}
                style={{
                  backdropFilter: 'blur(10px)',
                  boxShadow: selectedChannels.includes(channel.id)
                    ? '0 4px 16px rgba(0, 0, 0, 0.15)'
                    : '0 2px 8px rgba(0, 0, 0, 0.05)'
                }}
              >
                <div className="flex items-center gap-3">
                  <div
                    className={`w-4 h-4 rounded-full ${
                      selectedChannels.includes(channel.id)
                        ? getChannelColor(channel.id)
                        : 'bg-muted'
                    }`}
                  />
                  <span>{channel.label}</span>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <button
        onClick={handleSubmit}
        disabled={!selectedFile || selectedChannels.length === 0 || isSubmitting}
        className="w-full px-8 py-4 rounded-2xl bg-gradient-to-r from-blue-600 to-teal-600 text-white disabled:opacity-50 disabled:cursor-not-allowed transition-all duration-300 hover:scale-105 active:scale-95 flex items-center justify-center gap-2"
        style={{
          backdropFilter: 'blur(10px)',
          boxShadow: '0 8px 24px rgba(59, 130, 246, 0.3)'
        }}
      >
        {isSubmitting ? (
          <>
            <Loader2 className="w-5 h-5 animate-spin" />
            Verificando sessão...
          </>
        ) : (
          'Iniciar Processamento'
        )}
      </button>
    </div>
  );
}
