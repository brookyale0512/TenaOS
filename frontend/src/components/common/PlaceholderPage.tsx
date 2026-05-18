import { Card, CardContent } from "@/components/ui/card";

interface PlaceholderPageProps {
  title: string;
  description?: string;
  phase?: string;
}

export function PlaceholderPage({ title, description, phase }: PlaceholderPageProps) {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold text-[var(--clinic-ink)]">{title}</h1>
      <Card>
        <CardContent className="py-20 text-center">
          <p className="text-[hsl(var(--muted-foreground))] text-sm">{description ?? `${title} module`}</p>
          {phase && <p className="text-xs text-[var(--clinic-slate)] mt-2">Coming in {phase}</p>}
        </CardContent>
      </Card>
    </div>
  );
}
