// Formatting helpers shared by the web app.
export const formatPrice = (cents: number): string => {
  return `$${(cents / 100).toFixed(2)}`;
};

export interface PriceOptions {
  currency: string;
}

export type Currency = 'usd' | 'eur';

export enum Region { EU, US }

export async function fetchRates(base: Currency): Promise<number> {
  return 1;
}

const double: (a: number) => number = (a) => a * 2;
