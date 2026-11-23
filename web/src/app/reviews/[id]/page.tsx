import { Suspense } from "react";
import { ReviewLoader, ReviewSkeleton } from "@/components/review-view";

export const metadata = { title: "Review" };

export default function ReviewPage({ params }: PageProps<"/reviews/[id]">) {
  return (
    <Suspense fallback={<ReviewSkeleton />}>
      <ReviewLoader params={params} />
    </Suspense>
  );
}
