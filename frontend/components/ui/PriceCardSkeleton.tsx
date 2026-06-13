export default function PriceCardSkeleton() {
  return (
    <div className="rounded-card border border-stone-200 bg-white p-4 shadow-card">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 space-y-2">
          <div className="skeleton h-4 w-24" />
          <div className="skeleton h-3 w-36" />
        </div>
        <div className="space-y-2 text-right">
          <div className="skeleton h-7 w-20" />
          <div className="skeleton h-3 w-14 ml-auto" />
        </div>
      </div>
      <div className="mt-3 flex gap-2">
        <div className="skeleton h-6 w-24 rounded-pill" />
        <div className="skeleton h-6 w-20 rounded-pill" />
      </div>
    </div>
  );
}

export function PriceCardSkeletonList({ n = 4 }: { n?: number }) {
  return (
    <div className="flex flex-col gap-3">
      {Array.from({ length: n }).map((_, i) => (
        <PriceCardSkeleton key={i} />
      ))}
    </div>
  );
}
