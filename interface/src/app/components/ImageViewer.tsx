import { useEffect, useState, useRef } from 'react';
import { Eye, EyeOff, Layers, ZoomIn, ZoomOut, Move, Maximize, Loader2 } from 'lucide-react';
import * as Switch from '@radix-ui/react-switch';

interface Patch {
  id: string;
  x: number;
  y: number;
  original_url: string;
  mask_url: string;
}

export function ImageViewer() {
  const [manifest, setManifest] = useState<Patch[]>([]);
  const [showMask, setShowMask] = useState(true);
  const [isLoading, setIsLoading] = useState(true);
  
  const [zoom, setZoom] = useState(0.05);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [imgSize, setImgSize] = useState({ w: 0, h: 0, minX: 0, minY: 0 });
  
  const [isDragging, setIsDragging] = useState(false);
  const [lastMousePos, setLastMousePos] = useState({ x: 0, y: 0 });

  const containerRef = useRef<HTMLDivElement>(null);

  const centerImage = (currentZoom: number, width: number, height: number) => {
    if (containerRef.current) {
      const { clientWidth, clientHeight } = containerRef.current;
      const centerX = (clientWidth / 2) - (width * currentZoom / 2);
      const centerY = (clientHeight / 2) - (height * currentZoom / 2);
      setOffset({ x: centerX, y: centerY });
    }
  };

  useEffect(() => {
    fetch('http://localhost:8000/manifest')
      .then(res => res.json())
      .then((data: Patch[]) => {
        if (!data || data.length === 0) return;

        const xs = data.map(p => p.x);
        const ys = data.map(p => p.y);
        const minX = Math.min(...xs), minY = Math.min(...ys);
        const maxX = Math.max(...xs), maxY = Math.max(...ys);
        
        const fullW = (maxX - minX) + 4096;
        const fullH = (maxY - minY) + 4096;
        
        setImgSize({ w: fullW, h: fullH, minX, minY });
        setManifest(data);

        if (containerRef.current) {
          const { clientWidth, clientHeight } = containerRef.current;
          const zoomFit = Math.min((clientWidth - 40) / fullW, (clientHeight - 40) / fullH);
          setZoom(zoomFit);
          
          centerImage(zoomFit, fullW, fullH);
        }
        setIsLoading(false);
      });
  }, []);

  const handleMouseDown = (e: React.MouseEvent) => {
    setIsDragging(true);
    setLastMousePos({ x: e.clientX, y: e.clientY });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!isDragging) return;
    setOffset(prev => ({
      x: prev.x + (e.clientX - lastMousePos.x),
      y: prev.y + (e.clientY - lastMousePos.y)
    }));
    setLastMousePos({ x: e.clientX, y: e.clientY });
  };

  return (
    <div className="flex h-[70vh] bg-transparent overflow-hidden select-none">
      
      {/* Main Viewport */}
      <div 
        className="flex-1 relative m-2 rounded-3xl bg-slate-900 border border-white/5 overflow-hidden cursor-grab active:cursor-grabbing"
        ref={containerRef}
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={() => setIsDragging(false)}
        onMouseLeave={() => setIsDragging(false)}
      >
        {isLoading && (
          <div className="absolute inset-0 z-50 flex items-center justify-center bg-slate-900">
            <Loader2 className="w-8 h-8 text-blue-500 animate-spin" />
          </div>
        )}

        {/* The World Layer: Removido o flex-center para usar coordenadas absolutas precisas */}
        <div 
          className="absolute top-0 left-0 pointer-events-none will-change-transform"
          style={{ 
            transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom})`,
            transformOrigin: '0 0'
          }}
        >
          <div style={{ width: imgSize.w, height: imgSize.h, position: 'relative' }}>
            {manifest.map((patch) => (
              <div 
                key={patch.id}
                className="absolute bg-black/40"
                style={{ 
                  top: patch.y - imgSize.minY, 
                  left: patch.x - imgSize.minX, 
                  width: 4096, 
                  height: 4096 
                }}
              >
                <img src={patch.original_url} className="absolute inset-0 w-full h-full" draggable={false} loading="lazy" />
                {showMask && (
                  <img 
                    src={patch.mask_url} 
                    className="absolute inset-0 w-full h-full opacity-60"
                    style={{ mixBlendMode: 'multiply' }}
                    draggable={false}
                  />
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="absolute bottom-6 left-1/2 -translate-x-1/2 flex items-center gap-4 bg-slate-900/90 border border-white/10 p-2 rounded-2xl shadow-2xl pointer-events-auto">
          <button onClick={() => setZoom(z => z * 0.8)} className="p-2 hover:bg-white/5 rounded-xl text-white"><ZoomOut className="w-5 h-5"/></button>
          <span className="text-white font-mono text-xs w-12 text-center font-bold">{Math.round(zoom * 100)}%</span>
          <button onClick={() => setZoom(z => z * 1.2)} className="p-2 hover:bg-white/5 rounded-xl text-white"><ZoomIn className="w-5 h-5"/></button>
          <div className="w-px h-4 bg-white/10" />
          <button 
            onClick={() => {
              const z = Math.min((containerRef.current!.clientWidth - 40) / imgSize.w, (containerRef.current!.clientHeight - 40) / imgSize.h);
              setZoom(z);
              centerImage(z, imgSize.w, imgSize.h);
            }}
            className="p-2 text-blue-400 hover:bg-blue-500/10 rounded-xl"
            title="Centralizar e Ajustar"
          >
            <Maximize className="w-5 h-5" />
          </button>
        </div>
      </div>

      {/* Sidebar Controls */}
      <div className="w-72 p-4 border-l border-white/5 bg-transparent">
        <div className="p-5 rounded-3xl bg-slate-900/80 border border-white/10 space-y-4 shadow-xl backdrop-blur-md">
          <div className="flex items-center gap-2 text-blue-400 border-b border-white/5 pb-3">
            <Layers className="w-4 h-4" /> 
            <h3 className="text-xs font-bold uppercase tracking-widest text-white">Camadas</h3>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-xs text-slate-300 font-semibold">Máscara de Poros</span>
            <Switch.Root checked={showMask} onCheckedChange={setShowMask} className="w-10 h-5 bg-slate-700 rounded-full data-[state=checked]:bg-blue-600 relative transition-colors">
              <Switch.Thumb className="block w-4 h-4 bg-white rounded-full transition-transform translate-x-0.5 data-[state=checked]:translate-x-[20px]" />
            </Switch.Root>
          </div>
        </div>
      </div>
    </div>
  );
}