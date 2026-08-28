import { useState, useEffect } from 'react';
import { PieChart, Pie, Cell, Tooltip, Legend, ResponsiveContainer } from 'recharts';
import { ImageIcon, Percent, Mountain, Droplets, Info, Loader2 } from 'lucide-react';

export function Dashboard() {
  const [petroData, setPetroData] = useState<any>(null);
  const [metaData, setMetaData] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [petroRes, metaRes] = await Promise.all([
          fetch('http://localhost:8000/petrophysics'),
          fetch('http://localhost:8000/metadata')
        ]);

        const petroJson = await petroRes.json();
        const metaJson = await metaRes.json();

        if (!metaJson.error) {
          setPetroData(petroJson);
          setMetaData(metaJson);
        }
      } catch (error) {
        console.error("Erro ao carregar dados do Dashboard:", error);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  if (loading || !petroData || !metaData) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-muted-foreground gap-4">
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
        <p className="animate-pulse">Sincronizando dados petrofísicos...</p>
      </div>
    );
  }

  const areaData = [
    { name: 'Poros', value: petroData.porosidade_final, color: '#3b82f6' },
    { name: 'Sólido', value: parseFloat((100 - petroData.porosidade_final).toFixed(2)), color: '#94a3b8' }
  ];

  const stats = [
    {
      label: 'Total de Patches',
      value: metaData?.patch_generation?.total_patches || '0',
      icon: ImageIcon,
      color: 'from-blue-500 to-blue-600',
      bgColor: 'bg-blue-500/10',
      borderColor: 'border-blue-500/30'
    },
    {
      label: 'Porosidade Final',
      value: `${petroData.porosidade_final}%`,
      icon: Percent,
      color: 'from-teal-500 to-teal-600',
      bgColor: 'bg-teal-500/10',
      borderColor: 'border-teal-500/30'
    },
    {
      label: 'Área Total Amostrada',
      value: `${petroData.area_rocha_mm2} mm²`,
      icon: Mountain,
      color: 'from-slate-500 to-slate-600',
      bgColor: 'bg-slate-500/10',
      borderColor: 'border-slate-500/30'
    },
    {
      label: 'Área de Poros',
      value: `${petroData.area_poros_mm2} mm²`,
      icon: Droplets,
      color: 'from-indigo-500 to-indigo-600',
      bgColor: 'bg-indigo-500/10',
      borderColor: 'border-indigo-500/30'
    }
  ];

  return (
    <div className="p-6 space-y-6 overflow-y-auto h-full">
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {stats.map((stat) => {
          const Icon = stat.icon;
          return (
            <div
              key={stat.label}
              className={`rounded-2xl p-6 border ${stat.borderColor} ${stat.bgColor}`}
              style={{ backdropFilter: 'blur(20px)', boxShadow: '0 4px 16px rgba(0, 0, 0, 0.1)' }}
            >
              <div className="flex items-center justify-between mb-2">
                <div className={`p-3 rounded-xl bg-gradient-to-br ${stat.color}`}>
                  <Icon className="w-6 h-6 text-white" />
                </div>
              </div>
              <p className="text-3xl font-bold mb-1 tracking-tight">{stat.value}</p>
              <p className="text-sm text-muted-foreground font-medium">{stat.label}</p>
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 rounded-2xl bg-card/50 p-6 border border-border/50 flex flex-col">
          <div className="flex items-center gap-2 mb-6">
            <Percent className="w-5 h-5 text-blue-500" />
            <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Distribuição Volumétrica (2D)</h3>
          </div>
          <ResponsiveContainer width="100%" height={350}>
            <PieChart>
              <Pie
                data={areaData}
                cx="50%"
                cy="50%"
                innerRadius={80}
                outerRadius={120}
                paddingAngle={5}
                dataKey="value"
                label={({ name, value }) => `${name}: ${value}%`}
              >
                {areaData.map((entry, index) => (
                  <Cell key={`cell-${index}`} fill={entry.color} strokeWidth={2} />
                ))}
              </Pie>
              <Tooltip />
              <Legend verticalAlign="bottom" height={36}/>
            </PieChart>
          </ResponsiveContainer>
        </div>

        <div className="rounded-2xl bg-card/50 p-6 border border-border/50">
          <div className="flex items-center gap-2 mb-6">
            <Info className="w-5 h-5 text-teal-500" />
            <h3 className="text-lg font-semibold text-slate-900 dark:text-white">Metadados CZI</h3>
          </div>
          <div className="space-y-6">
            <div className="space-y-1">
              <p className="text-xs uppercase font-bold text-muted-foreground">Resolução Total</p>
              <p className="text-md font-mono text-slate-900 dark:text-white">
                {metaData?.image_structure?.full_width_px?.toLocaleString()} × {metaData?.image_structure?.full_height_px?.toLocaleString()} px
              </p>
            </div>
            <div className="space-y-1">
              <p className="text-xs uppercase font-bold text-muted-foreground">Resolução Física</p>
              <p className="text-md text-slate-900 dark:text-white">
                {(metaData?.physical_scaling?.pixel_size_x_meters * 1e6).toFixed(2)} µm / pixel
              </p>
            </div>
            <div className="space-y-1">
              <p className="text-xs uppercase font-bold text-muted-foreground">Arquivo de Origem</p>
              <p className="text-md truncate text-blue-500">{metaData?.source_file}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}